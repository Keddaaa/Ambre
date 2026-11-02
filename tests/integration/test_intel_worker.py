"""The worker runs the research job on its own schedule, between sync and cycle, offline."""

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import FakeManifoldClient, load_fixture
from sqlalchemy import func, select

from ambre import cli
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.intelligence.orm import IntelJobRunRow, IntelRequestRow

T = datetime(2026, 10, 31, 14, 5, tzinfo=UTC)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'ambre.db'}"
    for key in list(os.environ):
        if key.startswith(("AMBRE_", "MANIFOLD_", "EXECUTION_", "ENABLE_", "DATABASE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init-db"]) == 0
    return url


def earthquake_market() -> dict[str, Any]:
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    m = copy.deepcopy(base)
    m.update(
        id="synthetic_eq_worker",
        question="Will there be a 6.5 or greater magnitude earthquake in New Zealand in 2026?",
        slug="synthetic-eq-worker",
        probability=0.3,
        lastUpdatedTime=ms(T),
    )
    return m


def usgs_body() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "synthetic_worker_eq",
                "properties": {
                    "mag": 6.9,
                    "place": "far away, New Zealand",
                    "time": ms(T - timedelta(hours=5)),
                    "updated": ms(T - timedelta(hours=4)),
                    "status": "reviewed",
                },
                "geometry": {"type": "Point", "coordinates": [174.0, -41.0, 10.0]},
            }
        ],
    }


class Harness:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def http_factory(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return httpx.Response(200, json=usgs_body())

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def stop_after(n: int) -> Any:
    count = {"n": 0}

    def sleep(_: float) -> None:
        count["n"] += 1
        if count["n"] >= n:
            raise KeyboardInterrupt

    return sleep


def emitted(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    lines = capsys.readouterr().out.splitlines()[1:]
    return [json.loads(line) for line in lines if line.startswith("{")]


def run_worker(
    h: Harness | None, sleep_after: int = 1, clock: Any = None, http: bool = True
) -> int:
    fake = FakeManifoldClient([earthquake_market()])
    return cli.main(
        ["worker", "--interval", "0"],
        client_factory=lambda s: fake,  # type: ignore[arg-type,return-value]
        clock=clock or (lambda: T + timedelta(minutes=5)),
        sleep=stop_after(sleep_after),
        http_factory=h.http_factory if (h is not None and http) else None,
    )


def test_the_worker_runs_research_between_sync_and_cycle(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    h = Harness()
    assert run_worker(h) == 0
    (line,) = emitted(capsys)
    assert line["sync"] is not None and line["cycle"] is not None
    (research,) = line["research"]
    assert research["status"] == "OK" and research["detail"]["calls"] == 1
    assert research["detail"]["new_observations"] == 1 and len(h.calls) == 1
    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            assert s.scalar(select(func.count()).select_from(IntelRequestRow)) == 1
            statuses = list(s.scalars(select(IntelJobRunRow.status)))
            assert statuses == ["OK"]
    finally:
        engine.dispose()


def test_research_is_not_repeated_before_its_interval_and_returns_after_it(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    h = Harness()
    now = {"t": T + timedelta(minutes=5)}
    jumps = iter([60, 700])  # 1 minute later: not due; 11 more minutes later: due again

    def sleep(_: float) -> None:
        try:
            now["t"] += timedelta(seconds=next(jumps))
        except StopIteration:
            raise KeyboardInterrupt from None

    fake = FakeManifoldClient([earthquake_market()])
    assert (
        cli.main(
            ["worker", "--interval", "0"],
            client_factory=lambda s: fake,  # type: ignore[arg-type,return-value]
            clock=lambda: now["t"],
            sleep=sleep,
            http_factory=h.http_factory,
        )
        == 0
    )
    first, second, third = emitted(capsys)
    assert [r["status"] for r in first["research"]] == ["OK"]
    assert second["research"] == []  # not due: nothing ran, nothing was journaled
    assert [r["status"] for r in third["research"]] == ["OK"]
    assert len(h.calls) == 2  # the middle iteration made no call


def test_an_emergency_stop_blocks_research_without_using_up_its_turn(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = make_engine(db_url)
    try:
        factory = make_session_factory(engine)
        with factory() as s:
            repo.set_control(s, halted=True, reason="test stop", updated_at=T)
            s.commit()
        h = Harness()
        assert run_worker(h) == 0
        (line,) = emitted(capsys)
        assert [r["status"] for r in line["research"]] == ["SKIPPED_HALTED"] and h.calls == []
        with factory() as s:
            repo.set_control(s, halted=False, reason=None, updated_at=T)
            s.commit()
        assert run_worker(h) == 0
        (again,) = emitted(capsys)
        assert [r["status"] for r in again["research"]] == ["OK"] and len(h.calls) == 1
    finally:
        engine.dispose()


def test_a_research_failure_never_blocks_the_decision_cycle(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("scheduler store unavailable")

    monkeypatch.setattr(cli, "research_tick", boom)
    assert run_worker(Harness()) == 0
    (line,) = emitted(capsys)
    assert line["research"] is None and line["cycle"] is not None


def test_a_job_that_fails_is_journaled_and_the_cycle_still_runs(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("registry file unreadable")

    monkeypatch.setattr(cli, "_research_in_session", broken)
    assert run_worker(Harness()) == 0
    (line,) = emitted(capsys)
    assert line["research"][0]["status"] == "FAILED" and line["cycle"] is not None
    assert "registry file unreadable" in line["research"][0]["detail"]["error"]


def test_scheduling_can_be_switched_off_in_the_configuration(
    db_url: str, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "off.yaml"
    cfg.write_text("intelligence:\n  schedule_research: false\n")
    monkeypatch.setenv("AMBRE_CONFIG", str(cfg))
    h = Harness()
    assert run_worker(h) == 0
    (line,) = emitted(capsys)
    assert line["research"] is None and h.calls == [] and line["cycle"] is not None


def test_the_default_http_client_in_tests_never_reaches_a_real_provider(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_worker(None) == 0  # no http_factory given: the guarded default is used
    (line,) = emitted(capsys)
    assert line["research"][0]["status"] == "OK"  # the pass ran...
    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            row = s.scalars(select(IntelRequestRow)).one()
            assert row.status == "FAILED" and "transport error" in (row.detail or "")  # ...offline
    finally:
        engine.dispose()


def test_scheduler_status_shows_the_job_and_its_runs(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_worker(Harness())
    capsys.readouterr()
    assert cli.main(["scheduler-status"]) == 0
    snap = json.loads(capsys.readouterr().out.splitlines()[1])
    assert snap["jobs"][0]["name"] == "research" and snap["jobs"][0]["last_status"] == "OK"
    assert snap["runs"][0]["status"] == "OK"


def test_the_coverage_command_reconciles_after_a_worker_pass(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_worker(Harness())
    capsys.readouterr()
    assert cli.main(["coverage"]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[1])
    assert report["reconciled"] is True and report["total_unresolved_markets"] == 1
    assert sum(report["by_state"].values()) == report["total_unresolved_markets"]
    assert report["cost"]["money"] == 0 and report["cost"]["total_requests"] == 1
    assert all(r["denominator"] is not None for r in report["ratios"])

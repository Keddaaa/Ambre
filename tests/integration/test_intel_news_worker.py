"""The worker runs research and news ingestion as two separate scheduled jobs, offline.

Neither may delay or fail the other, both stop on the emergency stop, and a restart never
repeats a job whose turn is not due."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import FakeManifoldClient
from sqlalchemy import func, select
from test_intel_worker import earthquake_market, usgs_body

from ambre import cli
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.intelligence.orm import IntelJobRunRow, IntelObservationRow

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "news"
T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


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


class Net:
    """USGS and the news API behind one mocked transport; each can be scripted."""

    def __init__(self, snapi_status: int = 200) -> None:
        self.usgs: list[httpx.Request] = []
        self.snapi: list[httpx.Request] = []
        self.snapi_status = snapi_status

    def http_factory(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "earthquake.usgs.gov":
                self.usgs.append(request)
                return httpx.Response(200, json=usgs_body())
            if request.url.host == "api.spaceflightnewsapi.net":
                self.snapi.append(request)
                if self.snapi_status != 200:
                    return httpx.Response(self.snapi_status)
                return httpx.Response(
                    200,
                    json=json.loads(
                        (FIX / "synthetic_snapi_page.json").read_text(encoding="utf-8")
                    ),
                )
            raise AssertionError(f"unexpected host {request.url.host}")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def emitted(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    lines = capsys.readouterr().out.splitlines()[1:]
    return [json.loads(line) for line in lines if line.startswith("{")]


def run_worker(net: Net, clock: Any = None, iterations: int = 1) -> int:
    fake = FakeManifoldClient([earthquake_market()])
    count = {"n": 0}

    def sleep(_: float) -> None:
        count["n"] += 1
        if count["n"] >= iterations:
            raise KeyboardInterrupt

    return cli.main(
        ["worker", "--interval", "0"],
        client_factory=lambda s: fake,  # type: ignore[arg-type,return-value]
        clock=clock or (lambda: T),
        sleep=sleep,
        http_factory=net.http_factory,
    )


def by_job(line: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["job"]: r for r in line["research"]}


def test_both_jobs_run_in_one_worker_iteration_and_each_has_its_own_journal(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net()
    assert run_worker(net) == 0
    (line,) = emitted(capsys)
    jobs = by_job(line)
    assert set(jobs) == {"research", "news_ingest"}
    assert jobs["research"]["status"] == "OK" and jobs["news_ingest"]["status"] == "OK"
    news = jobs["news_ingest"]["detail"]
    assert news["new_observations"] == 4 and len(net.snapi) == 1
    assert news["polls"][0]["provider"] == "spaceflight_news"
    assert line["cycle"] is not None
    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            rows = list(s.scalars(select(IntelJobRunRow.job_name)))
            assert sorted(rows) == ["news_ingest", "research"]
            assert (
                s.scalar(
                    select(func.count())
                    .select_from(IntelObservationRow)
                    .where(IntelObservationRow.provider_id == "spaceflight_news")
                )
                == 4
            )
    finally:
        engine.dispose()


def test_a_dead_news_source_neither_fails_research_nor_the_cycle(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(snapi_status=503)
    assert run_worker(net) == 0
    (line,) = emitted(capsys)
    jobs = by_job(line)
    assert jobs["research"]["status"] == "OK" and len(net.usgs) == 1
    # the failure is data (a FAILED poll with a pause), not a crashed job
    assert jobs["news_ingest"]["status"] == "OK"
    assert jobs["news_ingest"]["detail"]["polls"][0]["status"] == "FAILED"
    assert line["cycle"] is not None


def test_a_crashing_news_job_leaves_research_and_the_cycle_alone(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("news bug")

    monkeypatch.setattr(cli, "_news_in_session", boom)
    net = Net()
    assert run_worker(net) == 0
    (line,) = emitted(capsys)
    jobs = by_job(line)
    assert (
        jobs["news_ingest"]["status"] == "FAILED"
        and "news bug" in jobs["news_ingest"]["detail"]["error"]
    )
    assert jobs["research"]["status"] == "OK" and line["cycle"] is not None


def test_an_emergency_stop_blocks_both_jobs_and_releases_them_after(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = make_engine(db_url)
    try:
        factory = make_session_factory(engine)
        with factory() as s:
            repo.set_control(s, halted=True, reason="test stop", updated_at=T)
            s.commit()
        net = Net()
        assert run_worker(net) == 0
        (line,) = emitted(capsys)
        assert {r["status"] for r in line["research"]} == {"SKIPPED_HALTED"}
        assert net.snapi == [] and net.usgs == []
        with factory() as s:
            repo.set_control(s, halted=False, reason=None, updated_at=T)
            s.commit()
        assert run_worker(net) == 0
        (again,) = emitted(capsys)
        assert {r["status"] for r in again["research"]} == {"OK"} and len(net.snapi) == 1
    finally:
        engine.dispose()


def test_a_restart_inside_the_interval_does_not_poll_again(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net()
    assert run_worker(net, clock=lambda: T) == 0
    emitted(capsys)
    # a new process, one minute later: the persisted schedule says neither job is due
    assert run_worker(net, clock=lambda: T + timedelta(minutes=1)) == 0
    (line,) = emitted(capsys)
    assert line["research"] == [] and len(net.snapi) == 1 and len(net.usgs) == 1
    # after the news interval (15 min) it is due again, and nothing is duplicated
    assert run_worker(net, clock=lambda: T + timedelta(minutes=16)) == 0
    (later,) = emitted(capsys)
    assert by_job(later)["news_ingest"]["status"] == "OK"
    assert by_job(later)["news_ingest"]["detail"]["new_observations"] == 0

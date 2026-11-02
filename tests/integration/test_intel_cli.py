"""`ambre research` end to end through the CLI, with a fake Manifold client and a mocked USGS."""

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
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import DecisionRow
from ambre.intelligence.orm import IntelRequestRow, IntelUsageRow

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
        id="synthetic_eq_cli",
        question="Will there be a 6.5 or greater magnitude earthquake in New Zealand in 2026?",
        slug="synthetic-eq-cli",
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
                "id": "synthetic_cli_eq",
                "properties": {
                    "mag": 6.9,
                    "place": "far from anywhere, New Zealand",
                    "time": ms(T - timedelta(hours=5)),
                    "updated": ms(T - timedelta(hours=4)),
                    "status": "reviewed",
                },
                "geometry": {"type": "Point", "coordinates": [174.0, -41.0, 10.0]},
            }
        ],
    }


def test_research_command_reports_cost_reason_and_never_decides(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = lambda: T + timedelta(minutes=5)  # noqa: E731
    assert cli.main(["init-db"]) == 0
    fake = FakeManifoldClient([earthquake_market()])
    assert cli.main(["sync"], client_factory=lambda s: fake, clock=lambda: T) == 0  # type: ignore[arg-type,return-value]
    capsys.readouterr()

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=usgs_body())

    def http_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert cli.main(["research"], clock=clock, http_factory=http_factory) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("mode=PAPER orders_enabled=false")
    report = json.loads(out[1])
    assert report["calls"] == 1 and len(calls) == 1
    assert report["new_observations"] == 1 and report["new_links"] == 1
    (request,) = report["requests"]
    assert request["status"] == "OK" and request["provider"] == "usgs_earthquakes"
    assert request["explanation"]["cost"]["money"] == 0

    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            assert s.scalar(select(func.count()).select_from(IntelRequestRow)) == 1
            assert s.scalar(select(func.count()).select_from(IntelUsageRow)) == 1
            assert (
                s.scalar(select(func.count()).select_from(DecisionRow)) == 0
            )  # no bet, no decision
    finally:
        engine.dispose()

    # a second run right away reuses the fresh answer: no new HTTP call
    assert cli.main(["research"], clock=clock, http_factory=http_factory) == 0
    again = json.loads(capsys.readouterr().out.splitlines()[1])
    assert again["calls"] == 0 and len(calls) == 1
    assert again["requests"][0]["status"] == "SKIPPED_FRESH"


def test_research_with_no_enabled_connector_makes_no_call(
    db_url: str, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cli.main(["init-db"]) == 0
    cfg = tmp_path / "providers.yaml"
    cfg.write_text(
        "providers:\n  usgs_earthquakes:\n    name: USGS\n    domain: seismic\n"
        "    status: VERIFIED_FREE\n    enabled: false\n"
        "    capabilities: {recent_earthquakes: 0.9}\n"
    )
    monkeypatch.setenv("PROVIDERS_PATH", str(cfg))
    fake = FakeManifoldClient([earthquake_market()])
    assert cli.main(["sync"], client_factory=lambda s: fake, clock=lambda: T) == 0  # type: ignore[arg-type,return-value]
    capsys.readouterr()

    def boom() -> httpx.AsyncClient:
        raise AssertionError("no HTTP client may be created when nothing can be called")

    assert cli.main(["research"], clock=lambda: T + timedelta(minutes=5), http_factory=boom) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[1])
    assert report["calls"] == 0
    assert report["requests"][0]["status"] == "SKIPPED_NO_PROVIDER"

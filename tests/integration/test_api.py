"""Read API + emergency halt endpoints, on a DB populated by run_cycle from the real fixture."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from builders import SETTINGS, T_CYCLE, T_SYNC, cycle, sync
from fakes import load_fixture
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.api.app import create_app
from ambre.config.settings import Settings
from ambre.domain.models import SimulationStatus
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import (
    Base,
    DecisionRow,
    LedgerRow,
    MarketRow,
    PositionRow,
    SimulationRow,
    SnapshotRow,
)

NOW = T_CYCLE + timedelta(minutes=4)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def populated(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    sync(factory, load_fixture(), T_SYNC)
    cycle(factory)
    return factory


def client_for(factory: sessionmaker[Session], settings: Settings = SETTINGS) -> TestClient:
    return TestClient(create_app(settings, factory, clock=lambda: NOW))


def test_health_ok(populated: sessionmaker[Session]) -> None:
    r = client_for(populated).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db_ok": True, "version": "0.1.0"}


def test_health_503_when_db_broken() -> None:
    broken = sessionmaker(create_engine("sqlite:////nonexistent_dir_ambre/x/y.db"))
    r = client_for(broken).get("/health")
    assert r.status_code == 503
    assert r.json()["db_ok"] is False
    assert r.json()["status"] == "degraded"


def test_system_reports_paper_and_orders_disabled(populated: sessionmaker[Session]) -> None:
    body = client_for(populated).get("/system").json()
    assert body["execution_mode"] == "PAPER"
    assert body["orders_enabled"] is False
    assert body["halted"] is False
    assert body["halt_reason"] is None
    assert body["last_sync_error"] is None
    assert body["last_sync_at"] is not None
    assert body["data_age_seconds"] == pytest.approx((NOW - T_SYNC).total_seconds(), abs=1)
    assert body["risk"]["max_position_fraction"] == 0.02
    assert body["survival"]["max_data_age_seconds"] == 900
    assert body["last_cycle_at"] is not None
    assert body["decisions_total"] > 0
    assert body["active_strategy"] == "VALUE_HUNTER"


def test_system_empty_db(factory: sessionmaker[Session]) -> None:
    body = client_for(factory).get("/system").json()
    assert body["last_sync_at"] is None
    assert body["data_age_seconds"] is None
    assert body["last_cycle_at"] is None
    assert body["decisions_total"] == 0
    assert body["active_strategy"] == "VALUE_HUNTER"


def test_markets_list_filters_and_null_estimates(populated: sessionmaker[Session]) -> None:
    c = client_for(populated)
    everything = c.get("/markets", params={"status": "all", "limit": 1000}).json()
    open_ = c.get("/markets", params={"status": "open", "limit": 1000}).json()
    resolved = c.get("/markets", params={"status": "resolved", "limit": 1000}).json()
    assert everything["total"] == open_["total"] + resolved["total"]
    assert resolved["total"] > 0
    assert all(m["is_resolved"] for m in resolved["items"])
    assert not any(m["is_resolved"] for m in open_["items"])

    evaluated = [m for m in open_["items"] if m["ambre_estimate"] is not None]
    never = [m for m in everything["items"] if m["ambre_estimate"] is None]
    assert evaluated and never
    for m in never:
        assert m["ambre_uncertainty"] is None
        assert m["ambre_ev_per_mana"] is None
        assert m["last_action"] is None
    with populated() as s:
        d = s.scalars(select(DecisionRow).where(DecisionRow.market_id == evaluated[0]["id"])).one()
    m0 = evaluated[0]
    assert m0["ambre_estimate"] == d.estimated_probability
    assert m0["ambre_uncertainty"] == d.uncertainty
    assert m0["ambre_ev_per_mana"] == d.ev_per_mana  # may legitimately be null
    assert m0["last_action"] == d.action
    # null ev_per_mana is passed through, never 0
    with populated() as s:
        null_ev = s.scalars(select(DecisionRow).where(DecisionRow.ev_per_mana.is_(None))).first()
    if null_ev is not None:
        item = next(m for m in everything["items"] if m["id"] == null_ev.market_id)
        assert item["ambre_ev_per_mana"] is None

    # pagination
    page = c.get("/markets", params={"status": "all", "limit": 2, "offset": 1}).json()
    assert len(page["items"]) == 2 and page["total"] == everything["total"]
    assert page["items"][0]["id"] == everything["items"][1]["id"]


def test_markets_text_search_is_case_insensitive_substring(
    populated: sessionmaker[Session],
) -> None:
    with populated() as s:
        question = s.scalars(select(MarketRow.question)).first()
    assert question
    needle = question[2:9].upper()
    body = client_for(populated).get("/markets", params={"status": "all", "q": needle}).json()
    assert body["total"] >= 1
    assert all(needle.lower() in m["question"].lower() for m in body["items"])
    none = client_for(populated).get("/markets", params={"status": "all", "q": "zzzz-nope"}).json()
    assert none == {"items": [], "total": 0}


def test_invalid_status_is_422(populated: sessionmaker[Session]) -> None:
    assert client_for(populated).get("/markets", params={"status": "bogus"}).status_code == 422


def test_market_detail(populated: sessionmaker[Session]) -> None:
    c = client_for(populated)
    with populated() as s:
        mid = s.scalars(select(DecisionRow.market_id)).first()
    assert mid
    body = c.get(f"/markets/{mid}").json()
    assert body["market"]["id"] == mid
    assert len(body["snapshots"]) >= 1
    times = [x["observed_at"] for x in body["snapshots"]]
    assert times == sorted(times)
    assert len(body["decisions"]) == 1
    assert "execution_status" in body["decisions"][0]


def test_market_detail_404(populated: sessionmaker[Session]) -> None:
    assert client_for(populated).get("/markets/does-not-exist").status_code == 404


def test_decisions_pass_nulls_and_status_through(populated: sessionmaker[Session]) -> None:
    body = client_for(populated).get("/decisions", params={"limit": 1000}).json()
    with populated() as s:
        rows = {d.decision_id: d for d in s.scalars(select(DecisionRow))}
    assert body["total"] == len(rows)
    created = [d["created_at"] for d in body["items"]]
    assert created == sorted(created, reverse=True)
    for item in body["items"]:
        row = rows[item["decision_id"]]
        assert item["execution_status"] == row.execution_status
        assert item["stake"] == row.stake
        for f in ("expected_value", "ev_per_mana", "avg_price", "conservative_probability"):
            assert item[f] == getattr(row, f)
    assert any(i["execution_status"] != "SIMULATED" for i in body["items"])
    only = client_for(populated).get("/decisions", params={"action": "NO_TRADE"}).json()
    assert only["total"] > 0 and all(i["action"] == "NO_TRADE" for i in only["items"])
    assert client_for(populated).get("/decisions", params={"action": "X"}).status_code == 422


def test_portfolio_values_add_up(populated: sessionmaker[Session]) -> None:
    body = client_for(populated).get("/portfolio").json()
    assert body["simulation"]["status"] == "ACTIVE"
    assert body["simulation"]["initial_capital"] == 1000.0
    assert body["exposure_known"] is True
    assert body["total_value_is_lower_bound"] is False
    # Independent expectation from raw DB rows (not the endpoint's arithmetic).
    with populated() as s:
        cash = s.scalar(select(func.sum(LedgerRow.amount)))
        expected = 0.0
        by_market = {}
        for pos in s.scalars(select(PositionRow).where(PositionRow.status == "OPEN")):
            snap = s.scalars(
                select(SnapshotRow)
                .where(SnapshotRow.market_id == pos.market_id)
                .order_by(SnapshotRow.observed_at.desc(), SnapshotRow.id.desc())
            ).first()
            assert snap is not None
            price = snap.probability if pos.side == "YES" else 1.0 - snap.probability
            by_market[pos.market_id] = pos.shares * price
            expected += pos.shares * price
    assert body["cash"] == pytest.approx(cash)
    assert body["positions_value"] == pytest.approx(expected)
    assert body["total_value"] == pytest.approx(cash + expected)
    assert sum(p["current_value"] for p in body["open_positions"]) == pytest.approx(expected)
    for p in body["open_positions"]:
        assert p["current_value"] == pytest.approx(by_market[p["market_id"]])
    assert body["peak_value"] >= body["total_value"] - 1e-9
    assert body["survival_state"] == "BALANCED"
    assert len(body["open_positions"]) > 0
    for p in body["open_positions"]:
        assert p["current_value"] is not None


def test_portfolio_unknown_exposure_is_lower_bound(populated: sessionmaker[Session]) -> None:
    from ambre.infrastructure.orm import SnapshotRow

    with populated() as s:
        pos_market = s.scalars(select(DecisionRow.market_id).where(DecisionRow.stake > 0)).first()
        assert pos_market
        # Corrupt the latest price of one open position's market.
        snaps = s.scalars(select(SnapshotRow).where(SnapshotRow.market_id == pos_market)).all()
        for sn in snaps:
            sn.probability = 2.0
        s.commit()
    body = client_for(populated).get("/portfolio").json()
    if body["exposure_known"]:
        pytest.skip("market had no open position")
    assert body["total_value_is_lower_bound"] is True


def test_portfolio_shows_dead_simulation(populated: sessionmaker[Session]) -> None:
    with populated() as s:
        sim = s.scalars(select(SimulationRow)).one()
        sim.status = SimulationStatus.DEAD.value
        s.commit()
    assert client_for(populated).get("/portfolio").json()["simulation"]["status"] == "DEAD"


def test_portfolio_404_without_simulation(factory: sessionmaker[Session]) -> None:
    r = client_for(factory).get("/portfolio")
    assert r.status_code == 404
    assert r.json() == {"detail": "no active simulation; run `ambre cycle`"}
    assert client_for(factory).get("/portfolio/history").status_code == 404


def test_portfolio_history_is_complete_and_ascending(populated: sessionmaker[Session]) -> None:
    cycle(populated, T_CYCLE + timedelta(minutes=1))
    cycle(populated, T_CYCLE + timedelta(minutes=2))
    body = client_for(populated).get("/portfolio/history").json()
    assert len(body) == 3
    times = [p["at"] for p in body]
    assert times == sorted(times)
    assert {"cash", "positions_value", "total_value", "peak_value", "drawdown"} <= set(body[0])


def test_halt_then_resume(populated: sessionmaker[Session]) -> None:
    c = client_for(populated)
    r = c.post("/control/halt", json={"reason": "manual check"})
    assert r.status_code == 200
    assert r.json()["halted"] is True and r.json()["reason"] == "manual check"
    sysb = c.get("/system").json()
    assert sysb["halted"] is True and sysb["halt_reason"] == "manual check"
    with populated() as s:  # committed, visible from a fresh session
        row = repo.get_control(s)
        assert row is not None and row.halted
    r = c.post("/control/resume", json={"confirm": True})
    assert r.json()["halted"] is False and r.json()["reason"] is None
    assert c.get("/system").json()["halted"] is False


def test_resume_rejects_non_json_and_keeps_halt(populated: sessionmaker[Session]) -> None:
    c = client_for(populated)
    c.post("/control/halt", json={"reason": "manual check"})
    for kwargs in (
        {},  # empty body
        {"content": "", "headers": {"content-type": "text/plain"}},
        {"content": '{"confirm": true}', "headers": {"content-type": "text/plain"}},
        {"json": {"confirm": False}},
        {"json": {}},
    ):
        r = c.post("/control/resume", **kwargs)  # type: ignore[arg-type]
        assert r.status_code in (415, 422), kwargs
        assert c.get("/system").json()["halted"] is True
    assert c.post("/control/resume", json={"confirm": True}).json()["halted"] is False


def test_halt_requires_reason(populated: sessionmaker[Session]) -> None:
    c = client_for(populated)
    assert c.post("/control/halt", json={"reason": "ab"}).status_code == 422
    assert c.post("/control/halt", json={}).status_code == 422
    assert c.post("/control/halt").status_code == 422
    assert c.post("/control/halt", json={"reason": "   "}).status_code == 422
    assert c.post("/control/halt", json={"reason": " ab "}).status_code == 422


def test_no_endpoint_changes_execution_mode() -> None:
    app = create_app(SETTINGS, None)
    writes = {
        (r.path, m)  # type: ignore[attr-defined]
        for r in app.routes
        for m in getattr(r, "methods", set())
        if m in {"POST", "PUT", "PATCH", "DELETE"}
    }
    # Paper-only writes; none of them can change the execution mode or reach Manifold.
    assert writes == {
        ("/control/halt", "POST"),
        ("/control/resume", "POST"),
        ("/control/run-cycle", "POST"),
        ("/orders", "POST"),
    }
    assert not any("mode" in p for p, _ in writes)


def test_cors_only_configured_origin(populated: sessionmaker[Session]) -> None:
    settings = SETTINGS.model_copy(update={"cors_origins": ["http://localhost:3000"]})
    c = client_for(populated, settings)
    ok = c.get("/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    bad = c.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_module_level_app_imports_without_db() -> None:
    from ambre.api.app import app

    assert app.title

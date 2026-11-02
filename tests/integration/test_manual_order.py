"""Manual paper orders (USER / CLAUDE): same risk engine and ledger as Ambre's own decisions."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from builders import SETTINGS, T_CYCLE, T_SYNC, cycle, sync
from fakes import load_fixture
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.api.app import create_app
from ambre.domain.models import Actor, Side
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, PositionRow
from ambre.pipeline import ManualOrder, ManualOrderError, manual_order, to_market, to_snapshot
from ambre.value_engine.eligibility import check_eligibility

NOW = T_CYCLE + timedelta(minutes=4)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    f = make_session_factory(engine)
    sync(f, load_fixture(), T_SYNC)
    cycle(f)
    yield f
    engine.dispose()


def free_market(factory: sessionmaker[Session]) -> tuple[str, float]:
    """An eligible market with a mid-range price and no open position: (id, P(YES))."""
    with factory() as s:
        taken = set(s.scalars(select(PositionRow.market_id)))
        for market_row, snap_row in repo.latest_snapshots_for_open_markets(s, 5000):
            if market_row.id in taken or not 0.1 < snap_row.probability < 0.8:
                continue
            if not check_eligibility(
                to_market(market_row), to_snapshot(snap_row), NOW, SETTINGS.eligibility
            ):
                return market_row.id, snap_row.probability
    raise AssertionError("fixture has no free eligible market")


def order(market_id: str, p: float, side: Side = Side.YES, **kw: object) -> ManualOrder:
    base: dict[str, object] = {
        "market_id": market_id,
        "side": side,
        "probability": p,
        "stake": 10.0,
        "actor": Actor.USER,
        "request_id": "req-0001-aaaa",
    }
    base.update(kw)
    return ManualOrder(**base)  # type: ignore[arg-type]


def run(factory: sessionmaker[Session], o: ManualOrder) -> str:
    with factory() as s:
        decision_id = manual_order(s, SETTINGS, NOW, o)
        s.commit()
    return decision_id


def test_order_with_edge_opens_a_paper_position_tagged_with_its_actor(
    factory: sessionmaker[Session],
) -> None:
    mid, q = free_market(factory)
    decision_id = run(factory, order(mid, min(q + 0.35, 0.95)))
    with factory() as s:
        d = s.get(DecisionRow, decision_id)
        assert d is not None
        assert (d.actor, d.strategy, d.action) == ("USER", "MANUAL", "YES")
        assert d.execution_status == "SIMULATED"
        assert d.model_version.startswith("manual+")
        assert d.market_probability == pytest.approx(q)
        assert 0 < d.stake <= 20.0  # capped by max_position_fraction * total value
        pos = s.scalars(select(PositionRow).where(PositionRow.market_id == mid)).one()
        assert pos.decision_id == decision_id and pos.status == "OPEN"
        bets = s.scalars(select(LedgerRow).where(LedgerRow.decision_id == decision_id)).all()
        assert [b.kind for b in bets] == ["BET"]


def test_order_without_edge_is_rejected_and_journaled_without_a_bet(
    factory: sessionmaker[Session],
) -> None:
    mid, q = free_market(factory)
    decision_id = run(factory, order(mid, q))  # agrees with the market: no edge
    with factory() as s:
        d = s.get(DecisionRow, decision_id)
        assert d is not None
        assert d.execution_status == "REJECTED" and d.actor == "USER"
        assert any("edge" in r for r in d.risk_reasons)
        assert (
            s.scalar(
                select(func.count()).select_from(PositionRow).where(PositionRow.market_id == mid)
            )
            == 0
        )


def test_same_request_id_is_idempotent(factory: sessionmaker[Session]) -> None:
    mid, q = free_market(factory)
    first = run(factory, order(mid, min(q + 0.35, 0.95)))
    second = run(factory, order(mid, min(q + 0.35, 0.95)))
    assert first == second
    with factory() as s:
        count = s.scalar(
            select(func.count()).select_from(PositionRow).where(PositionRow.market_id == mid)
        )
        assert count == 1


def test_second_order_on_a_market_with_a_position_is_rejected(
    factory: sessionmaker[Session],
) -> None:
    mid, q = free_market(factory)
    run(factory, order(mid, min(q + 0.35, 0.95)))
    second = run(factory, order(mid, min(q + 0.35, 0.95), request_id="req-0002-bbbb"))
    with factory() as s:
        d = s.get(DecisionRow, second)
        assert d is not None and d.execution_status == "REJECTED"
        assert any("already has an open position" in r for r in d.risk_reasons)


def test_unknown_market_raises(factory: sessionmaker[Session]) -> None:
    with pytest.raises(ManualOrderError) as exc:
        run(factory, order("synthetic_nope", 0.6))
    assert exc.value.kind == "unknown_market"


def test_ambre_decisions_keep_the_ambre_actor(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        actors = set(s.scalars(select(DecisionRow.actor)))
    assert actors == {"AMBRE"}


def client(factory: sessionmaker[Session]) -> TestClient:
    return TestClient(create_app(SETTINGS, factory, clock=lambda: NOW))


def body(mid: str, p: float, **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "market_id": mid,
        "side": "YES",
        "probability": p,
        "stake": 10.0,
        "actor": "CLAUDE",
        "request_id": "req-api-0001",
    }
    base.update(kw)
    return base


def test_api_order_returns_the_decision(factory: sessionmaker[Session]) -> None:
    mid, q = free_market(factory)
    r = client(factory).post("/orders", json=body(mid, min(q + 0.35, 0.95), note="why"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["actor"] == "CLAUDE" and out["execution_status"] == "SIMULATED"
    assert out["market_question"]
    listed = client(factory).get("/decisions", params={"limit": 5}).json()["items"]
    assert any(d["decision_id"] == out["decision_id"] for d in listed)


def test_api_order_rejects_the_ambre_actor_and_bad_inputs(factory: sessionmaker[Session]) -> None:
    mid, _ = free_market(factory)
    c = client(factory)
    assert c.post("/orders", json=body(mid, 0.6, actor="AMBRE")).status_code == 422
    assert c.post("/orders", json=body(mid, 1.0)).status_code == 422
    assert c.post("/orders", json=body(mid, 0.6, stake=0)).status_code == 422
    assert c.post("/orders", json=body(mid, 0.6, side="MAYBE")).status_code == 422


def test_api_order_unknown_market_is_404_with_the_api_prefix(
    factory: sessionmaker[Session],
) -> None:
    r = client(factory).post("/orders", json=body("synthetic_nope", 0.6))
    assert r.status_code == 404
    assert r.json()["detail"].startswith("unknown market")


def test_api_run_cycle_reports_counts(factory: sessionmaker[Session]) -> None:
    r = client(factory).post("/control/run-cycle")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["survival_state"] in {"BALANCED", "DEFENSIVE", "OBSERVING", "HALTED"}
    assert out["settled"] == {"settled": 0, "refunded": 0, "unknown": []}
    assert out["evaluated"] >= 0

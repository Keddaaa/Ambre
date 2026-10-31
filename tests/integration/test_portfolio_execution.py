"""Virtual portfolio, paper execution, execution gate and settlement (SQLite in memory)."""

import math
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from builders import NOW, make_market, make_snapshot
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ambre.config.settings import Settings
from ambre.domain.models import (
    Action,
    ExecutionMode,
    ExecutionStatus,
    RiskVerdict,
    Side,
    SimulationStatus,
    StrategyName,
    SurvivalAssessment,
    SurvivalState,
)
from ambre.execution_engine.executor import (
    DecisionDraft,
    ExecutionGate,
    ExecutionModeNotAvailable,
    PaperExecutor,
)
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.orm import (
    DecisionRow,
    LedgerRow,
    MarketRow,
    PortfolioSnapshotRow,
    PositionRow,
    SimulationRow,
)
from ambre.portfolio.service import (
    PortfolioService,
    SettlementReport,
    create_simulation,
    mark_simulation_dead,
)
from ambre.value_engine.cpmm import cpmm_fill
from ambre.value_engine.value import FillValue, assess_fill

APPROVED = RiskVerdict(approved=True, stake=20.0, reasons=["edge after impact ok"])
REJECTED = RiskVerdict(approved=False, stake=0.0, reasons=["edge after impact too small"])


# --------------------------------------------------------------------------- helpers


def add_market(session: Session, mid: str = "synthetic_m1") -> int:
    repo.upsert_market(session, make_market(id=mid), raw={}, observed_at=NOW)
    snap_id = repo.insert_snapshot_if_new(session, make_snapshot(market_id=mid), ingested_at=NOW)
    assert snap_id is not None
    return snap_id


def new_sim(session: Session, name: str = "sim") -> str:
    return create_simulation(session, name, 1000.0, NOW)


def fill_value(stake: float = 20.0, side: Side = Side.YES, p_side: float = 0.6) -> FillValue:
    return assess_fill(p_side, cpmm_fill(100.0, 100.0, 0.5, stake, side), 0.0)


def draft(
    sim_id: str,
    snap_id: int,
    mid: str = "synthetic_m1",
    action: Action = Action.YES,
    **overrides: Any,
) -> DecisionDraft:
    d = DecisionDraft(
        market_id=mid,
        snapshot_id=snap_id,
        created_at=NOW,
        model_version="synthetic-v0",
        strategy=StrategyName.VALUE_HUNTER.value,
        market_probability=0.5,
        estimated_probability=0.62,
        conservative_probability=0.6,
        uncertainty=0.02,
        expected_value=2.0,
        ev_per_mana=0.1,
        action=action.value,
        stake=20.0,
        shares=0.0,
        avg_price=0.0,
        reasons=["synthetic reason"],
        invalidation=["synthetic invalidation"],
        source_ids=["synthetic_source"],
        survival_state=SurvivalState.BALANCED.value,
        risk_reasons=[],
        idempotency_key=f"{sim_id}:{mid}:{snap_id}:{StrategyName.VALUE_HUNTER.value}",
    )
    return replace(d, **overrides)


def count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def bet(
    session: Session,
    sim_id: str,
    mid: str = "synthetic_m1",
    side: Side = Side.YES,
    stake: float = 20.0,
) -> str:
    snap_id = add_market(session, mid)
    action = Action.YES if side is Side.YES else Action.NO
    return PaperExecutor(session, sim_id, ExecutionMode.PAPER).execute(
        draft(sim_id, snap_id, mid, action), APPROVED, fill_value(stake, side)
    )


def resolve(session: Session, mid: str, resolution: str | None, rp: float | None = None) -> None:
    row = session.get(MarketRow, mid)
    assert row is not None
    row.is_resolved = resolution is not None
    row.resolution = resolution
    row.resolution_probability = rp
    row.resolution_at = NOW + timedelta(days=1)
    session.flush()


def ledger_kinds(session: Session, sim_id: str) -> list[tuple[str, float]]:
    rows = session.scalars(
        select(LedgerRow).where(LedgerRow.simulation_id == sim_id).order_by(LedgerRow.id)
    )
    return [(r.kind, r.amount) for r in rows]


def balanced() -> SurvivalAssessment:
    return SurvivalAssessment(
        state=SurvivalState.BALANCED, stake_multiplier=1.0, allow_new_positions=True, reasons=["ok"]
    )


# --------------------------------------------------------------------------- simulations


def test_new_simulation_has_1000_cash(session: Session) -> None:
    sim_id = new_sim(session)
    svc = PortfolioService(session, sim_id)
    assert svc.cash() == 1000.0
    assert ledger_kinds(session, sim_id) == [("INIT", 1000.0)]
    assert svc.open_positions() == []
    assert svc.peak_value() == 1000.0
    view = svc.view({})
    assert (view.cash, view.total_value, view.open_exposure) == (1000.0, 1000.0, 0.0)
    assert view.exposure_known is True


def test_new_simulation_archives_old_keeps_history(session: Session) -> None:
    old = new_sim(session, "old")
    bet(session, old)
    later = NOW + timedelta(days=2)
    new = create_simulation(session, "new", 1000.0, later)

    old_row = session.get(SimulationRow, old)
    assert old_row is not None
    assert old_row.status == SimulationStatus.ARCHIVED.value
    assert old_row.ended_at == later
    active = repo.get_active_simulation(session)
    assert active is not None and active.id == new
    # History of the archived simulation is untouched.
    assert ledger_kinds(session, old) == [("INIT", 1000.0), ("BET", -20.0)]
    assert len(PortfolioService(session, old).open_positions()) == 1
    assert session.scalar(select(func.count()).where(DecisionRow.simulation_id == old)) == 1
    # The new simulation starts fresh.
    assert PortfolioService(session, new).cash() == 1000.0
    assert PortfolioService(session, new).open_positions() == []


def test_mark_simulation_dead(session: Session) -> None:
    sim_id = new_sim(session)
    mark_simulation_dead(session, sim_id, "failure: value below 50%", NOW)
    row = session.get(SimulationRow, sim_id)
    assert row is not None
    assert row.status == SimulationStatus.DEAD.value
    assert row.end_reason == "failure: value below 50%"
    assert row.ended_at == NOW
    assert repo.get_active_simulation(session) is None
    assert ledger_kinds(session, sim_id) == [("INIT", 1000.0)]


# --------------------------------------------------------------------------- execution


def test_paper_bet_debits_cash_and_opens_position(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    fv = fill_value(20.0, Side.YES)
    decision_id = PaperExecutor(session, sim_id, ExecutionMode.PAPER).execute(
        draft(sim_id, snap_id), APPROVED, fv
    )

    svc = PortfolioService(session, sim_id)
    assert svc.cash() == pytest.approx(1000.0 - fv.cost)
    (pos,) = svc.open_positions()
    assert (pos.market_id, pos.side, pos.status) == ("synthetic_m1", "YES", "OPEN")
    assert pos.shares == pytest.approx(fv.fill.shares)
    assert pos.cost == pytest.approx(fv.cost)
    assert pos.decision_id == decision_id
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None
    assert dec.execution_status == ExecutionStatus.SIMULATED.value
    assert dec.stake == pytest.approx(fv.fill.amount)
    assert dec.shares == pytest.approx(fv.fill.shares)
    assert dec.avg_price == pytest.approx(fv.fill.avg_price)
    assert dec.risk_reasons == APPROVED.reasons
    bet_row = session.scalars(select(LedgerRow).where(LedgerRow.kind == "BET")).one()
    assert bet_row.amount == pytest.approx(-fv.cost)
    assert bet_row.decision_id == decision_id
    assert bet_row.market_id == "synthetic_m1"

    view = svc.view({"synthetic_m1": 0.6})
    assert view.open_exposure == pytest.approx(fv.cost)
    assert view.open_market_ids == frozenset({"synthetic_m1"})
    assert view.total_value == pytest.approx(1000.0 - fv.cost + fv.fill.shares * 0.6)
    assert view.exposure_known is True


def test_paper_bet_no_side_valued_at_no_price(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id, side=Side.NO)
    svc = PortfolioService(session, sim_id)
    (pos,) = svc.open_positions()
    assert pos.side == "NO"
    view = svc.view({"synthetic_m1": 0.3})
    assert view.total_value == pytest.approx(svc.cash() + pos.shares * 0.7)


def test_rejected_order_leaves_portfolio_unchanged(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    fv = fill_value(20.0)
    before = (ledger_kinds(session, sim_id), count(session, PositionRow))

    decision_id = PaperExecutor(session, sim_id, ExecutionMode.PAPER).execute(
        draft(sim_id, snap_id), REJECTED, fv
    )

    assert (ledger_kinds(session, sim_id), count(session, PositionRow)) == before
    assert PortfolioService(session, sim_id).cash() == 1000.0
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None
    assert dec.execution_status == ExecutionStatus.REJECTED.value
    assert dec.risk_reasons == REJECTED.reasons
    # The simulated fill is recorded for the audit trail, nothing is executed.
    assert dec.shares == pytest.approx(fv.fill.shares)
    assert dec.ev_per_mana == pytest.approx(fv.ev_per_mana)


def test_rejected_without_fill_value(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    decision_id = PaperExecutor(session, sim_id, ExecutionMode.PAPER).execute(
        draft(sim_id, snap_id), REJECTED, None
    )
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None and dec.execution_status == ExecutionStatus.REJECTED.value
    assert ledger_kinds(session, sim_id) == [("INIT", 1000.0)]
    assert count(session, PositionRow) == 0


def test_no_trade_decision_not_executed(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    verdict = RiskVerdict(approved=False, stake=0.0, reasons=["no proposal"])
    decision_id = PaperExecutor(session, sim_id, ExecutionMode.PAPER).execute(
        draft(sim_id, snap_id, action=Action.NO_TRADE, stake=0.0), verdict, None
    )
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None
    assert dec.execution_status == ExecutionStatus.NOT_EXECUTED.value
    assert dec.action == Action.NO_TRADE.value
    assert ledger_kinds(session, sim_id) == [("INIT", 1000.0)]
    assert count(session, PositionRow) == 0


def test_idempotent_execute(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    executor = PaperExecutor(session, sim_id, ExecutionMode.PAPER)
    d = draft(sim_id, snap_id)
    first = executor.execute(d, APPROVED, fill_value())
    statements: list[str] = []

    def spy(_conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        statements.append(sql)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", spy)
    try:
        second = executor.execute(d, APPROVED, fill_value())
    finally:
        event.remove(engine, "before_cursor_execute", spy)

    assert first == second
    # The second call is a pure read: not even a write attempt rolled back later.
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert count(session, DecisionRow) == 1
    assert [k for k, _ in ledger_kinds(session, sim_id)].count("BET") == 1
    assert count(session, PositionRow) == 1


def test_research_mode_never_touches_ledger(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    decision_id = PaperExecutor(session, sim_id, ExecutionMode.RESEARCH).execute(
        draft(sim_id, snap_id), APPROVED, fill_value()
    )
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None and dec.execution_status == ExecutionStatus.NOT_EXECUTED.value
    assert ledger_kinds(session, sim_id) == [("INIT", 1000.0)]
    assert count(session, PositionRow) == 0


def test_approved_paper_without_fill_value_is_a_programming_error(session: Session) -> None:
    sim_id = new_sim(session)
    snap_id = add_market(session)
    with pytest.raises(ValueError):
        PaperExecutor(session, sim_id, ExecutionMode.PAPER).execute(
            draft(sim_id, snap_id), APPROVED, None
        )
    assert count(session, DecisionRow) == 0
    assert ledger_kinds(session, sim_id) == [("INIT", 1000.0)]


def test_failed_execute_leaves_no_partial_rows(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id)  # open position on synthetic_m1
    snap2 = repo.insert_snapshot_if_new(
        session,
        make_snapshot(event_at=NOW, observed_at=NOW),
        ingested_at=NOW,
    )
    assert snap2 is not None
    before = (count(session, DecisionRow), ledger_kinds(session, sim_id))
    executor = PaperExecutor(session, sim_id, ExecutionMode.PAPER)
    d = draft(sim_id, snap2, idempotency_key="second-bet-same-market")
    with pytest.raises(Exception):  # noqa: B017 - UNIQUE(simulation_id, market_id)
        executor.execute(d, APPROVED, fill_value())
    assert (count(session, DecisionRow), ledger_kinds(session, sim_id)) == before
    assert count(session, PositionRow) == 1
    # The outer session is still usable after the failed savepoint.
    assert PortfolioService(session, sim_id).cash() == pytest.approx(1000.0 - 20.0)


def test_manifold_mode_refused_even_if_enabled() -> None:
    settings = Settings(execution_mode=ExecutionMode.MANIFOLD_MANA, enable_manifold_orders=True)
    with pytest.raises(ExecutionModeNotAvailable, match="not implemented in v0.1"):
        ExecutionGate(settings).check()
    disabled = Settings(execution_mode=ExecutionMode.MANIFOLD_MANA, enable_manifold_orders=False)
    with pytest.raises(ExecutionModeNotAvailable):
        ExecutionGate(disabled).check()
    assert ExecutionGate(Settings(execution_mode=ExecutionMode.PAPER)).check() is (
        ExecutionMode.PAPER
    )
    assert ExecutionGate(Settings(execution_mode=ExecutionMode.RESEARCH)).check() is (
        ExecutionMode.RESEARCH
    )


def test_paper_executor_refuses_manifold_mode(session: Session) -> None:
    with pytest.raises(ExecutionModeNotAvailable):
        PaperExecutor(session, "any", ExecutionMode.MANIFOLD_MANA)


# --------------------------------------------------------------------------- settlement


def test_settle_yes_no(session: Session) -> None:
    sim_id = new_sim(session)
    win = bet(session, sim_id, "synthetic_win", Side.YES)
    lose = bet(session, sim_id, "synthetic_lose", Side.NO)
    svc = PortfolioService(session, sim_id)
    shares = {p.market_id: p.shares for p in svc.open_positions()}
    cash_before = svc.cash()
    resolve(session, "synthetic_win", "YES")
    resolve(session, "synthetic_lose", "YES")
    settled_at = NOW + timedelta(days=2)

    report = svc.settle_resolved(settled_at)

    assert report == SettlementReport(settled=2, refunded=0, unknown=[])
    assert svc.cash() == pytest.approx(cash_before + shares["synthetic_win"])
    assert svc.open_positions() == []
    positions = {p.market_id: p for p in session.scalars(select(PositionRow))}
    assert positions["synthetic_win"].payout == pytest.approx(shares["synthetic_win"])
    assert positions["synthetic_lose"].payout == 0.0
    assert all(p.status == "SETTLED" and p.settled_at == settled_at for p in positions.values())
    win_dec, lose_dec = session.get(DecisionRow, win), session.get(DecisionRow, lose)
    assert win_dec is not None and lose_dec is not None
    assert (win_dec.outcome, win_dec.settled_at) == ("YES", settled_at)
    assert win_dec.payout == pytest.approx(shares["synthetic_win"])
    assert (lose_dec.outcome, lose_dec.payout) == ("YES", 0.0)
    settles = [a for k, a in ledger_kinds(session, sim_id) if k == "SETTLE"]
    assert sorted(settles) == pytest.approx([0.0, shares["synthetic_win"]])

    # Settling again is a no-op: no double payout.
    assert svc.settle_resolved(settled_at) == SettlementReport(settled=0, refunded=0, unknown=[])
    assert svc.cash() == pytest.approx(cash_before + shares["synthetic_win"])


def test_settle_no_resolution_pays_no_side(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id, side=Side.NO)
    svc = PortfolioService(session, sim_id)
    (pos,) = svc.open_positions()
    cash_before = svc.cash()
    resolve(session, "synthetic_m1", "NO")
    assert svc.settle_resolved(NOW).settled == 1
    assert svc.cash() == pytest.approx(cash_before + pos.shares)


def test_settle_cancel_refunds_cost(session: Session) -> None:
    sim_id = new_sim(session)
    decision_id = bet(session, sim_id)
    svc = PortfolioService(session, sim_id)
    (pos,) = svc.open_positions()
    resolve(session, "synthetic_m1", "CANCEL")

    report = svc.settle_resolved(NOW + timedelta(days=2))

    assert report == SettlementReport(settled=0, refunded=1, unknown=[])
    assert svc.cash() == pytest.approx(1000.0)
    assert ledger_kinds(session, sim_id)[-1] == ("REFUND", pytest.approx(pos.cost))
    assert svc.open_positions() == []
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None
    assert dec.outcome == "CANCEL"
    assert dec.payout == pytest.approx(pos.cost)


def test_settle_mkt_pays_resolution_probability(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id, "synthetic_yes", Side.YES)
    bet(session, sim_id, "synthetic_no", Side.NO)
    svc = PortfolioService(session, sim_id)
    shares = {p.market_id: p.shares for p in svc.open_positions()}
    cash_before = svc.cash()
    resolve(session, "synthetic_yes", "MKT", 0.7)
    resolve(session, "synthetic_no", "MKT", 0.7)

    report = svc.settle_resolved(NOW)

    assert report == SettlementReport(settled=2, refunded=0, unknown=[])
    expected = shares["synthetic_yes"] * 0.7 + shares["synthetic_no"] * 0.3
    assert svc.cash() == pytest.approx(cash_before + expected)


def test_unknown_resolution_left_open_and_reported(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id, "synthetic_weird", Side.YES)
    bet(session, sim_id, "synthetic_mkt_none", Side.YES)
    bet(session, sim_id, "synthetic_closed", Side.YES)
    svc = PortfolioService(session, sim_id)
    cash_before = svc.cash()
    ledger_before = ledger_kinds(session, sim_id)
    resolve(session, "synthetic_weird", "SOMETHING_NEW")
    resolve(session, "synthetic_mkt_none", "MKT", None)
    closed = session.get(MarketRow, "synthetic_closed")
    assert closed is not None
    closed.close_at = NOW - timedelta(hours=1)  # closed but not resolved: not eligible
    session.flush()

    report = svc.settle_resolved(NOW)

    assert report.settled == 0 and report.refunded == 0
    assert sorted(report.unknown) == ["synthetic_mkt_none", "synthetic_weird"]
    assert svc.cash() == cash_before
    assert ledger_kinds(session, sim_id) == ledger_before
    assert {p.market_id for p in svc.open_positions()} == {
        "synthetic_weird",
        "synthetic_mkt_none",
        "synthetic_closed",
    }
    decisions = session.scalars(select(DecisionRow)).all()
    assert all(d.outcome is None and d.payout is None and d.settled_at is None for d in decisions)


PREDICTION_FIELDS = (
    "market_probability",
    "estimated_probability",
    "conservative_probability",
    "uncertainty",
    "expected_value",
    "ev_per_mana",
    "model_version",
    "reasons",
    "invalidation",
    "action",
    "stake",
    "shares",
    "avg_price",
    "strategy",
    "risk_reasons",
    "execution_status",
)


def test_settlement_does_not_alter_prediction_fields(session: Session) -> None:
    sim_id = new_sim(session)
    decision_id = bet(session, sim_id)
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None
    before = {f: getattr(dec, f) for f in PREDICTION_FIELDS}
    resolve(session, "synthetic_m1", "YES")
    PortfolioService(session, sim_id).settle_resolved(NOW)
    session.expire_all()
    dec = session.get(DecisionRow, decision_id)
    assert dec is not None
    assert {f: getattr(dec, f) for f in PREDICTION_FIELDS} == before
    assert dec.outcome == "YES"


def test_settlement_only_touches_own_simulation(session: Session) -> None:
    old = new_sim(session, "old")
    bet(session, old)
    new = create_simulation(session, "new", 1000.0, NOW + timedelta(days=1))
    resolve(session, "synthetic_m1", "YES")
    assert PortfolioService(session, new).settle_resolved(NOW) == SettlementReport(0, 0, [])
    assert len(PortfolioService(session, old).open_positions()) == 1


# --------------------------------------------------------------------------- valuation


def test_view_unknown_price_marks_exposure_unknown(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id, "synthetic_a")
    bet(session, sim_id, "synthetic_b")
    svc = PortfolioService(session, sim_id)
    pos = {p.market_id: p for p in svc.open_positions()}

    view = svc.view({"synthetic_a": 0.5})

    assert view.exposure_known is False
    # Documented: total_value = cash + the positions that do have a price.
    assert view.total_value == pytest.approx(svc.cash() + pos["synthetic_a"].shares * 0.5)
    assert view.open_exposure == pytest.approx(sum(p.cost for p in pos.values()))
    assert view.open_market_ids == frozenset({"synthetic_a", "synthetic_b"})


def test_view_invalid_price_marks_exposure_unknown(session: Session) -> None:
    sim_id = new_sim(session)
    bet(session, sim_id)
    view = PortfolioService(session, sim_id).view({"synthetic_m1": float("nan")})
    assert view.exposure_known is False


def test_view_database_error_marks_exposure_unknown(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sim_id = new_sim(session)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    for name in ("execute", "scalar", "scalars"):
        monkeypatch.setattr(session, name, boom)

    view = PortfolioService(session, sim_id).view({})

    assert view.exposure_known is False
    assert math.isnan(view.total_value)


def test_record_snapshot_and_peak_value(session: Session) -> None:
    sim_id = new_sim(session)
    svc = PortfolioService(session, sim_id)
    assert svc.peak_value() == 1000.0
    bet(session, sim_id)
    view = svc.view({"synthetic_m1": 0.9})
    assert view.total_value > 1000.0
    peak = max(svc.peak_value(), view.total_value)
    svc.record_snapshot(view, peak, balanced(), NOW)

    (row,) = session.scalars(select(PortfolioSnapshotRow)).all()
    assert row.simulation_id == sim_id
    assert row.at == NOW
    assert row.cash == pytest.approx(view.cash)
    assert row.total_value == pytest.approx(view.total_value)
    assert row.positions_value == pytest.approx(view.total_value - view.cash)
    assert row.peak_value == pytest.approx(peak)
    assert row.drawdown == pytest.approx(0.0)
    assert row.survival_state == "BALANCED"
    assert row.survival_reasons == ["ok"]
    assert svc.peak_value() == pytest.approx(peak)

    lower = svc.view({"synthetic_m1": 0.1})
    svc.record_snapshot(lower, svc.peak_value(), balanced(), NOW + timedelta(minutes=5))
    assert svc.peak_value() == pytest.approx(peak)
    last = repo.list_portfolio_snapshots(session, sim_id)[-1]
    assert last.drawdown == pytest.approx(1 - lower.total_value / peak)

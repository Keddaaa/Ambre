"""End-to-end paper cycle: fake Manifold client on the real fixture, SQLite, fixed clock."""

import asyncio
import copy
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeManifoldClient, load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.config.settings import Settings
from ambre.data_engine.sync import MarketSyncService
from ambre.domain.models import (
    Action,
    ExecutionMode,
    ExecutionStatus,
    RiskVerdict,
    Side,
    SimulationStatus,
    StrategyName,
    SurvivalState,
)
from ambre.execution_engine.executor import DecisionDraft, PaperExecutor
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import (
    Base,
    DecisionRow,
    LedgerRow,
    MarketRow,
    PortfolioSnapshotRow,
    PositionRow,
    SimulationRow,
)
from ambre.pipeline import CycleReport, run_cycle
from ambre.portfolio.service import PortfolioService, create_simulation
from ambre.risk_engine.risk import RiskEngine
from ambre.value_engine.cpmm import cpmm_fill
from ambre.value_engine.value import assess_fill

T_SYNC = datetime(2026, 10, 31, 14, 5, tzinfo=UTC)  # just after the fixture's last update
T_CYCLE = T_SYNC + timedelta(minutes=1)
SETTINGS = Settings(database_url="sqlite://", execution_mode=ExecutionMode.PAPER)


def ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def sync(factory: sessionmaker[Session], markets: list[dict[str, Any]], at: datetime) -> None:
    service = MarketSyncService(FakeManifoldClient(markets), factory, lambda: at)  # type: ignore[arg-type]
    report = asyncio.run(service.sync(max_pages=5, page_size=1000))
    assert report.completed and report.new_snapshots > 0


def cycle(
    factory: sessionmaker[Session], now: datetime = T_CYCLE, settings: Settings = SETTINGS
) -> CycleReport:
    with factory() as s:
        report = run_cycle(s, settings, now)
        s.commit()
    return report


def count(factory: sessionmaker[Session], model: Any) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def only_simulation(factory: sessionmaker[Session]) -> SimulationRow:
    with factory() as s:
        (sim,) = s.scalars(select(SimulationRow)).all()
        return sim


def open_market_count(factory: sessionmaker[Session]) -> int:
    with factory() as s:
        return len(repo.latest_snapshots_for_open_markets(s, 10_000))


def assert_counts_consistent(report: CycleReport) -> None:
    # Each eligible market ends in exactly one bucket.
    assert report.evaluated == (
        report.no_trade + report.simulated + report.rejected + report.skipped_duplicate
    )
    assert report.skipped_by_survival == 0 or report.evaluated == report.skipped_duplicate


def journaled(factory: sessionmaker[Session]) -> int:
    return count(factory, DecisionRow)


METRICS = ("expected_value", "ev_per_mana", "avg_price")


# --------------------------------------------------------------------------- tests


def test_full_cycle_from_fixture(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)

    report = cycle(factory)

    assert report.survival_state is SurvivalState.BALANCED
    assert report.evaluated > 0
    assert report.simulated > 0
    assert report.skipped_duplicate == 0
    assert report.evaluated + report.ineligible <= open_market_count(factory)
    assert_counts_consistent(report)
    # Resolved outcomes before `now` feed the model: the fixture has one YES and one NO.
    assert report.model_version == "base-rate-shrinkage-v0.1(w=0.9,n=2)"

    sim = only_simulation(factory)
    assert sim.status == SimulationStatus.ACTIVE.value
    assert sim.initial_capital == 1000.0
    with factory() as s:
        decisions = s.scalars(select(DecisionRow)).all()
        assert len(decisions) == report.evaluated  # ineligible markets are not journaled
        for d in decisions:
            assert d.idempotency_key == (
                f"{sim.id}:{d.market_id}:{d.snapshot_id}:{StrategyName.VALUE_HUNTER.value}"
            )
            assert d.strategy == StrategyName.VALUE_HUNTER.value
            assert d.survival_state == SurvivalState.BALANCED.value
            assert d.model_version == report.model_version
        statuses = [d.execution_status for d in decisions]
        assert statuses.count(ExecutionStatus.SIMULATED.value) == report.simulated
        assert statuses.count(ExecutionStatus.REJECTED.value) == report.rejected
        assert statuses.count(ExecutionStatus.NOT_EXECUTED.value) == report.no_trade
        assert all(
            d.action == Action.NO_TRADE.value
            for d in decisions
            if d.execution_status == ExecutionStatus.NOT_EXECUTED.value
        )

        svc = PortfolioService(s, sim.id)
        positions = svc.open_positions()
        assert len(positions) == report.simulated
        # At the start, cash plus the cost of the positions is the initial capital.
        assert svc.cash() + sum(p.cost for p in positions) == pytest.approx(1000.0)
        for p in positions:
            assert p.cost <= 0.02 * 1000.0 + 1e-9
        assert sum(p.cost for p in positions) <= 0.20 * 1000.0 + 1e-9
        (snap,) = s.scalars(select(PortfolioSnapshotRow)).all()
        assert snap.survival_state == SurvivalState.BALANCED.value


def test_rerun_cycle_creates_nothing_new(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)
    first = cycle(factory)
    counts = [count(factory, m) for m in (DecisionRow, LedgerRow, PositionRow, SimulationRow)]

    second = cycle(factory, T_CYCLE + timedelta(minutes=1))

    assert [count(factory, m) for m in (DecisionRow, LedgerRow, PositionRow, SimulationRow)] == (
        counts
    )
    assert second.skipped_duplicate == first.evaluated
    assert second.simulated == second.rejected == second.no_trade == 0
    assert_counts_consistent(second)
    # Open positions are priced from their latest snapshots: exposure stays known.
    assert second.survival_state is SurvivalState.BALANCED
    assert count(factory, PortfolioSnapshotRow) == 2


def test_max_markets_per_cycle_bounds_the_work(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)
    report = cycle(factory, settings=SETTINGS.model_copy(update={"max_markets_per_cycle": 3}))
    assert report.evaluated + report.ineligible <= 3


def test_stale_data_observing_no_bets(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)

    report = cycle(factory, T_SYNC + timedelta(hours=2))

    assert report.survival_state is SurvivalState.OBSERVING
    assert report.simulated == 0
    assert report.evaluated == 0
    assert report.skipped_by_survival > 0
    assert_counts_consistent(report)
    assert journaled(factory) == 0  # vetoed markets are not journaled
    assert count(factory, PositionRow) == 0
    assert only_simulation(factory).status == SimulationStatus.ACTIVE.value
    with factory() as s:
        kinds = s.scalars(select(LedgerRow.kind)).all()
    assert kinds == ["INIT"]


def test_never_synced_is_observing(factory: sessionmaker[Session]) -> None:
    report = cycle(factory)
    assert report.survival_state is SurvivalState.OBSERVING
    assert report.evaluated == report.ineligible == 0
    assert only_simulation(factory).status == SimulationStatus.ACTIVE.value


def test_halt_flag_stops_new_positions(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)
    with factory.begin() as s:
        repo.set_control(s, halted=True, reason="manual stop", updated_at=T_SYNC)

    report = cycle(factory)

    assert report.survival_state is SurvivalState.HALTED
    assert report.simulated == 0
    assert count(factory, PositionRow) == 0
    # A manual halt never kills the simulation.
    assert only_simulation(factory).status == SimulationStatus.ACTIVE.value


def test_halt_then_resume_evaluates_the_same_snapshots(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)
    with factory.begin() as s:
        repo.set_control(s, halted=True, reason="manual stop", updated_at=T_SYNC)

    halted = cycle(factory)

    assert halted.survival_state is SurvivalState.HALTED
    assert halted.evaluated == 0
    assert halted.skipped_by_survival > 0
    assert journaled(factory) == 0

    with factory.begin() as s:
        repo.set_control(s, halted=False, reason=None, updated_at=T_CYCLE)
    resumed = cycle(factory, T_CYCLE + timedelta(minutes=1))  # no new snapshot in between

    assert resumed.survival_state is SurvivalState.BALANCED
    assert resumed.skipped_by_survival == 0
    assert resumed.skipped_duplicate == 0
    assert resumed.evaluated == halted.skipped_by_survival
    assert resumed.simulated > 0
    assert journaled(factory) == resumed.evaluated
    assert_counts_consistent(resumed)


def test_capacity_exhausted_skips_then_resumes_after_settlement(
    factory: sessionmaker[Session],
) -> None:
    fixture = load_fixture()
    sync(factory, fixture, T_SYNC)
    tight = SETTINGS.model_copy(
        update={"risk": SETTINGS.risk.model_copy(update={"max_total_exposure_fraction": 0.01})}
    )

    first = cycle(factory, settings=tight)

    assert first.simulated > 0
    assert first.skipped_no_capacity > 0
    assert_counts_consistent(first)
    with factory() as s:
        svc = PortfolioService(s, only_simulation(factory).id)
        prices = {}
        for p in svc.open_positions():
            snap = repo.latest_snapshot_for_market(s, p.market_id)
            assert snap is not None
            prices[p.market_id] = snap.probability
        room = RiskEngine(tight.risk).capacity(svc.view(prices))
        first_markets = set(s.scalars(select(DecisionRow.market_id)).all())
        pos = s.scalars(select(PositionRow).order_by(PositionRow.id)).first()
        assert pos is not None
        cancelled = pos.market_id
    assert room < tight.risk.min_stake  # less than one minimum stake of room left
    assert journaled(factory) == first.evaluated  # skipped markets are not journaled

    # The market of the first position is cancelled: its cost is refunded, room frees up.
    t2 = T_CYCLE + timedelta(minutes=5)
    raw = copy.deepcopy(next(m for m in fixture if m["id"] == cancelled))
    raw.update(
        isResolved=True,
        resolution="CANCEL",
        resolutionTime=ms(t2 - timedelta(seconds=20)),
        lastUpdatedTime=ms(t2 - timedelta(seconds=10)),
    )
    sync(factory, [raw], t2)

    second = cycle(factory, t2 + timedelta(minutes=1), settings=tight)

    assert second.settled.refunded == 1
    assert_counts_consistent(second)
    with factory() as s:
        second_markets = set(s.scalars(select(DecisionRow.market_id)).all())
    assert second_markets - first_markets  # previously skipped markets are now journaled
    assert second.no_trade + second.simulated + second.rejected > 0


def test_missing_metrics_are_stored_as_none(factory: sessionmaker[Session]) -> None:
    fixture = load_fixture()
    sync(factory, fixture, T_SYNC)
    first = cycle(factory)
    assert first.no_trade > 0 and first.simulated > 0
    with factory() as s:
        no_trades = s.scalars(
            select(DecisionRow).where(DecisionRow.action == Action.NO_TRADE.value)
        ).all()
        pos = s.scalars(select(PositionRow).order_by(PositionRow.id)).first()
        assert pos is not None
        traded = pos.market_id
    for d in no_trades:
        assert all(getattr(d, m) is None for m in METRICS)
        assert d.conservative_probability is None
        assert d.stake == 0.0 and d.shares == 0.0  # true zeros
    with factory() as s:
        sims = s.scalars(
            select(DecisionRow).where(
                DecisionRow.execution_status == ExecutionStatus.SIMULATED.value
            )
        ).all()
    for d in sims:
        assert all(getattr(d, m) is not None for m in METRICS)
        assert d.conservative_probability is not None

    # A new snapshot of a market already held: rejected without any simulated fill.
    t2 = T_CYCLE + timedelta(minutes=5)
    raw = copy.deepcopy(next(m for m in fixture if m["id"] == traded))
    raw.update(lastUpdatedTime=ms(t2 - timedelta(seconds=10)))
    sync(factory, [raw], t2)
    cycle(factory, t2 + timedelta(minutes=1))

    with factory() as s:
        rejected = s.scalars(
            select(DecisionRow).where(
                DecisionRow.market_id == traded,
                DecisionRow.execution_status == ExecutionStatus.REJECTED.value,
            )
        ).all()
    (rej,) = rejected
    assert any("already has an open position" in r for r in rej.risk_reasons)
    assert all(getattr(rej, m) is None for m in METRICS)


def _big_bet(s: Session, sim_id: str, market_id: str, stake: float) -> None:
    snap = repo.latest_snapshot_for_market(s, market_id)
    assert snap is not None
    fv = assess_fill(0.6, cpmm_fill(snap.pool_yes, snap.pool_no, snap.p, stake, Side.YES), 0.0)
    draft = DecisionDraft(
        market_id=market_id,
        snapshot_id=snap.id,
        created_at=T_SYNC,
        model_version="synthetic-manual",
        strategy=StrategyName.VALUE_HUNTER.value,
        market_probability=snap.probability,
        estimated_probability=0.6,
        conservative_probability=0.6,
        uncertainty=0.0,
        expected_value=0.0,
        ev_per_mana=0.0,
        action=Action.YES.value,
        stake=stake,
        shares=0.0,
        avg_price=0.0,
        reasons=["synthetic oversized bet"],
        invalidation=[],
        source_ids=[snap.source_id],
        survival_state=SurvivalState.BALANCED.value,
        risk_reasons=[],
        idempotency_key=f"synthetic:{market_id}",
    )
    verdict = RiskVerdict(approved=True, stake=stake, reasons=["synthetic"])
    PaperExecutor(s, sim_id, ExecutionMode.PAPER).execute(draft, verdict, fv)


def test_failure_marks_simulation_dead(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)
    raw = copy.deepcopy(
        next(m for m in load_fixture() if m.get("probability") == 0.5515954038983015)
    )
    with factory.begin() as s:
        sim_id = create_simulation(s, "doomed", 1000.0, T_SYNC)
        _big_bet(s, sim_id, raw["id"], 600.0)
    # The market then crashes to 1%: the YES position is worth almost nothing.
    raw.update(probability=0.01, lastUpdatedTime=ms(T_SYNC + timedelta(seconds=30)))
    sync(factory, [raw], T_SYNC + timedelta(seconds=40))

    report = cycle(factory)

    assert report.survival_state is SurvivalState.HALTED
    assert report.simulated == report.evaluated == 0
    sim = only_simulation(factory)
    assert sim.status == SimulationStatus.DEAD.value
    assert sim.ended_at == T_CYCLE
    assert sim.end_reason is not None and sim.end_reason.startswith("failure:")
    # History is kept.
    assert count(factory, DecisionRow) == 1
    assert count(factory, PositionRow) == 1
    assert count(factory, LedgerRow) == 2
    with factory() as s:
        (snap,) = s.scalars(select(PortfolioSnapshotRow)).all()
    assert snap.survival_state == SurvivalState.HALTED.value
    assert snap.total_value <= 500.0

    # A DEAD simulation stays dead: no new simulation, no new decision.
    again = cycle(factory, T_CYCLE + timedelta(minutes=1))
    assert again.survival_state is SurvivalState.HALTED
    assert count(factory, SimulationRow) == 1
    assert count(factory, DecisionRow) == 1
    assert count(factory, PortfolioSnapshotRow) == 1


def test_resolution_settles_on_next_cycle(factory: sessionmaker[Session]) -> None:
    fixture = load_fixture()
    sync(factory, fixture, T_SYNC)
    first = cycle(factory)
    assert first.simulated > 0
    with factory() as s:
        pos = s.scalars(select(PositionRow).order_by(PositionRow.id)).first()
        assert pos is not None
        market_id, side, shares = pos.market_id, pos.side, pos.shares

    t2 = T_CYCLE + timedelta(minutes=5)
    raw = copy.deepcopy(next(m for m in fixture if m["id"] == market_id))
    raw.update(
        isResolved=True,
        resolution=side,
        resolutionTime=ms(t2 - timedelta(seconds=20)),
        lastUpdatedTime=ms(t2 - timedelta(seconds=10)),
    )
    sync(factory, [raw], t2)

    report = cycle(factory, t2 + timedelta(minutes=1))

    assert report.settled.settled == 1
    assert report.settled.unknown == []
    with factory() as s:
        settle = s.scalars(select(LedgerRow).where(LedgerRow.kind == "SETTLE")).all()
        assert [(r.market_id, r.amount) for r in settle] == [(market_id, pytest.approx(shares))]
        row = s.scalars(select(PositionRow).where(PositionRow.market_id == market_id)).one()
        assert row.status == "SETTLED"
        decision = s.get(DecisionRow, row.decision_id)
        assert decision is not None and decision.outcome == side
        assert s.get(MarketRow, market_id).is_resolved  # type: ignore[union-attr]


def test_model_fit_ignores_resolutions_after_now(factory: sessionmaker[Session]) -> None:
    sync(factory, load_fixture(), T_SYNC)
    with factory.begin() as s:
        row = s.get(MarketRow, "2USQ2QQR8U")  # resolved NO in the fixture
        assert row is not None
        row.resolution_at = T_CYCLE + timedelta(hours=1)

    report = cycle(factory)

    assert report.model_version == "base-rate-shrinkage-v0.1(w=0.9,n=1)"

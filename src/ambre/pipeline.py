"""End-to-end paper decision cycle.

``run_cycle`` works inside the caller's session and never commits: the caller (the
CLI) commits on success and rolls back on any exception, so a cycle is one
transaction. No code here calls a Manifold endpoint; prices come from the database.
"""

import logging
import math
from dataclasses import dataclass, field, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ambre.config.settings import Settings
from ambre.data_engine.sync import SYNC_KEY
from ambre.domain.models import (
    Action,
    BinaryMarket,
    ExecutionMode,
    ExecutionStatus,
    MarketSnapshot,
    RiskVerdict,
    SimulationStatus,
    SurvivalAssessment,
    SurvivalState,
)
from ambre.execution_engine.executor import DecisionDraft, ExecutionGate, PaperExecutor
from ambre.feature_engine.features import extract_features
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.orm import DecisionRow, MarketRow, SnapshotRow
from ambre.portfolio.service import (
    PortfolioService,
    SettlementReport,
    create_simulation,
    mark_simulation_dead,
)
from ambre.prediction_engine.baselines import BaseRateShrinkage
from ambre.risk_engine.risk import PortfolioView, RiskEngine
from ambre.strategy_engine.strategies import ValueHunter
from ambre.survival_engine.survival import SurvivalInputs, assess_survival, is_failure
from ambre.value_engine.eligibility import check_eligibility

logger = logging.getLogger(__name__)

DEFAULT_SIMULATION_NAME = "default"
NO_MODEL = "none"


@dataclass
class CycleReport:
    """Counts of one cycle.

    ``evaluated`` counts eligible markets; each one ends in exactly one of
    ``no_trade`` (NOT_EXECUTED decisions), ``simulated``, ``rejected`` or
    ``skipped_duplicate`` (idempotency key already journaled).
    """

    survival_state: SurvivalState
    evaluated: int = 0
    ineligible: int = 0
    no_trade: int = 0
    simulated: int = 0
    rejected: int = 0
    skipped_duplicate: int = 0
    settled: SettlementReport = field(default_factory=lambda: SettlementReport(0, 0))
    model_version: str = NO_MODEL


def idempotency_key(sim_id: str, market_id: str, snapshot_id: int, strategy: str) -> str:
    return f"{sim_id}:{market_id}:{snapshot_id}:{strategy}"


def _to_market(row: MarketRow) -> BinaryMarket:
    return BinaryMarket.model_validate({k: getattr(row, k) for k in BinaryMarket.model_fields})


def _to_snapshot(row: SnapshotRow) -> MarketSnapshot:
    return MarketSnapshot.model_validate({k: getattr(row, k) for k in MarketSnapshot.model_fields})


class _Cycle:
    """State of one cycle; the steps follow the order of ``run_cycle``."""

    def __init__(
        self, session: Session, settings: Settings, now: datetime, mode: ExecutionMode, sim_id: str
    ) -> None:
        self.s = session
        self.settings = settings
        self.now = now
        self.sim_id = sim_id
        self.portfolio = PortfolioService(session, sim_id)
        self.executor = PaperExecutor(session, sim_id, mode)
        self.risk = RiskEngine(settings.risk)
        self.strategy = ValueHunter()

    def latest_prices(self) -> dict[str, float]:
        """Latest P(YES) of every market with an OPEN position, whatever its eligibility."""
        prices: dict[str, float] = {}
        for pos in self.portfolio.open_positions():
            snap = repo.latest_snapshot_for_market(self.s, pos.market_id)
            if snap is not None:
                prices[pos.market_id] = snap.probability
        return prices

    def view(self) -> PortfolioView:
        return self.portfolio.view(self.latest_prices())

    def peak(self, view: PortfolioView) -> float:
        peak = self.portfolio.peak_value()
        if view.exposure_known and math.isfinite(view.total_value):
            peak = max(peak, view.total_value)
        return peak

    def survival(self, view: PortfolioView, initial_capital: float) -> SurvivalAssessment:
        state = repo.get_sync_state(self.s, SYNC_KEY)
        last = state.last_success_at if state is not None else None
        age = (self.now - last).total_seconds() if last is not None else None
        control = repo.get_control(self.s)
        return assess_survival(
            SurvivalInputs(
                total_value=view.total_value,
                peak_value=self.peak(view),
                initial_capital=initial_capital,
                data_age_seconds=age,
                exposure_known=view.exposure_known,
                halted=control.halted if control is not None else False,
            ),
            self.settings.survival,
            self.settings.risk,
        )

    def key_exists(self, key: str) -> bool:
        found = self.s.scalar(
            select(DecisionRow.decision_id).where(DecisionRow.idempotency_key == key)
        )
        return found is not None

    def evaluate_markets(
        self,
        report: CycleReport,
        model: BaseRateShrinkage,
        view: PortfolioView,
        survival: SurvivalAssessment,
    ) -> PortfolioView:
        """Evaluate the open markets; return the portfolio view after the trades."""
        pairs = repo.latest_snapshots_for_open_markets(self.s, self.settings.max_markets_per_cycle)
        for market_row, snap_row in pairs:
            market, snap = _to_market(market_row), _to_snapshot(snap_row)
            if snap.observed_at > self.now or check_eligibility(
                market, snap, self.now, self.settings.eligibility
            ):
                report.ineligible += 1
                continue
            report.evaluated += 1
            # The key names the strategy that evaluated (VALUE_HUNTER), NO_TRADE outcomes too.
            key = idempotency_key(self.sim_id, market.id, snap_row.id, self.strategy.name.value)
            if self.key_exists(key):
                report.skipped_duplicate += 1
                continue
            status = self.decide(market, snap, snap_row.id, key, model, view, survival)
            if status is ExecutionStatus.SIMULATED:
                report.simulated += 1
                view = self.view()  # cash, exposure and open markets changed
            elif status is ExecutionStatus.REJECTED:
                report.rejected += 1
            else:
                report.no_trade += 1
        return view

    def decide(
        self,
        market: BinaryMarket,
        snap: MarketSnapshot,
        snapshot_id: int,
        key: str,
        model: BaseRateShrinkage,
        view: PortfolioView,
        survival: SurvivalAssessment,
    ) -> ExecutionStatus:
        estimate = model.predict(extract_features(market, snap, self.now))
        proposal = self.strategy.evaluate(
            market, snap, estimate, view.total_value, self.settings.risk
        )
        # Defaults describe a NO_TRADE; a proposal overrides the trade fields. Without a
        # simulated fill there is no EV to record (the columns are NOT NULL): 0.0.
        draft = DecisionDraft(
            market_id=market.id,
            snapshot_id=snapshot_id,
            created_at=self.now,
            model_version=estimate.model_version,
            strategy=self.strategy.name.value,
            market_probability=snap.probability,
            estimated_probability=estimate.probability,
            conservative_probability=estimate.probability,
            uncertainty=estimate.uncertainty,
            expected_value=0.0,
            ev_per_mana=0.0,
            action=Action.NO_TRADE.value,
            stake=0.0,
            shares=0.0,
            avg_price=0.0,
            reasons=[
                f"p={estimate.probability:g}",
                f"q={snap.probability:g}",
                f"u={estimate.uncertainty:g}",
                "no edge once the estimate is penalised by its uncertainty",
            ],
            invalidation=[],
            source_ids=[snap.source_id],
            survival_state=survival.state.value,
            risk_reasons=[],
            idempotency_key=key,
        )
        if proposal is None:
            no_trade = RiskVerdict(approved=False, stake=0.0, reasons=[])
            decision_id = self.executor.execute(draft, no_trade, None)
        else:
            verdict, fill_value = self.risk.evaluate(proposal, snap, view, survival)
            draft = replace(
                draft,
                conservative_probability=proposal.conservative_probability,
                action=proposal.side.value,
                stake=verdict.stake if verdict.approved else proposal.stake,
                reasons=list(proposal.reasons),
                invalidation=list(proposal.invalidation),
            )
            decision_id = self.executor.execute(draft, verdict, fill_value)
        row = self.s.get(DecisionRow, decision_id)
        assert row is not None
        return ExecutionStatus(row.execution_status)


def run_cycle(session: Session, settings: Settings, now: datetime) -> CycleReport:
    """Run one paper decision cycle at ``now`` inside ``session`` (the caller commits).

    1. execution gate; 2. current simulation (created with the initial capital if
    none; a DEAD one means no action and HALTED); 3. settlement of resolved
    markets; 4. portfolio view at the latest prices; 5. survival (the failure rule
    alone marks the simulation DEAD); 6. model fit on outcomes resolved before
    ``now``; 7. evaluation of open markets; 8. portfolio snapshot.
    """
    mode = ExecutionGate(settings).check()

    sim = repo.get_current_simulation(session)
    if sim is None:
        sim_id = create_simulation(session, DEFAULT_SIMULATION_NAME, settings.initial_capital, now)
        sim = repo.get_current_simulation(session)
        assert sim is not None and sim.id == sim_id
    if sim.status == SimulationStatus.DEAD.value:
        logger.warning("simulation %s is DEAD (%s): no action", sim.id, sim.end_reason)
        return CycleReport(survival_state=SurvivalState.HALTED)

    c = _Cycle(session, settings, now, mode, sim.id)
    report = CycleReport(survival_state=SurvivalState.OBSERVING)
    report.settled = c.portfolio.settle_resolved(now)

    view = c.view()
    survival = c.survival(view, sim.initial_capital)
    report.survival_state = survival.state

    if is_failure(survival):
        reason = "; ".join(survival.reasons)
        mark_simulation_dead(session, sim.id, reason, now)
        logger.error("simulation %s marked DEAD: %s", sim.id, reason)
    elif not math.isfinite(view.total_value):
        logger.warning("portfolio valuation unavailable: markets not evaluated this cycle")
    else:
        model = BaseRateShrinkage.fit(
            repo.resolved_binary_outcomes(session, before=now), settings.prediction
        )
        report.model_version = model.version
        view = c.evaluate_markets(report, model, view, survival)

    c.portfolio.record_snapshot(view, c.peak(view), survival, now)
    return report

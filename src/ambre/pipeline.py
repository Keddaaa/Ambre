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
    Actor,
    BinaryMarket,
    ExecutionMode,
    ExecutionStatus,
    MarketSnapshot,
    Proposal,
    RiskVerdict,
    Side,
    SimulationStatus,
    SurvivalAssessment,
    SurvivalState,
)
from ambre.evaluation_engine.metrics import EvaluationReport, ScoredForecast, evaluate
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
from ambre.strategy_engine.strategies import P_CONS_MAX, P_CONS_MIN, ValueHunter
from ambre.survival_engine.survival import SurvivalInputs, assess_survival, is_failure
from ambre.value_engine.eligibility import check_eligibility

logger = logging.getLogger(__name__)

DEFAULT_SIMULATION_NAME = "default"
NO_MODEL = "none"


@dataclass
class CycleReport:
    """Counts of one cycle.

    Every eligible market ends in exactly one bucket: ``skipped_duplicate``
    (idempotency key already journaled), ``skipped_by_survival`` (survival forbids
    new positions or exposure is unknown: nothing is evaluated nor journaled),
    ``skipped_no_capacity`` (less than one minimum stake of room left: not
    journaled, so the snapshot is evaluated on a later cycle), or a journaled
    decision: ``no_trade`` (NOT_EXECUTED), ``simulated`` or ``rejected``.
    ``evaluated`` = ``no_trade + simulated + rejected + skipped_duplicate``.
    """

    survival_state: SurvivalState
    evaluated: int = 0
    ineligible: int = 0
    no_trade: int = 0
    simulated: int = 0
    rejected: int = 0
    skipped_duplicate: int = 0
    skipped_by_survival: int = 0
    skipped_no_capacity: int = 0
    settled: SettlementReport = field(default_factory=lambda: SettlementReport(0, 0))
    model_version: str = NO_MODEL


def idempotency_key(sim_id: str, market_id: str, snapshot_id: int, strategy: str) -> str:
    return f"{sim_id}:{market_id}:{snapshot_id}:{strategy}"


def to_market(row: MarketRow) -> BinaryMarket:
    return BinaryMarket.model_validate({k: getattr(row, k) for k in BinaryMarket.model_fields})


def to_snapshot(row: SnapshotRow) -> MarketSnapshot:
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
        """Evaluate the open markets; return the portfolio view after the trades.

        Survival vetoes and exhausted capacity are global, not market-specific: the
        markets they block are counted but never journaled, so their snapshots stay
        available for a later cycle.
        """
        blocked = not (
            survival.allow_new_positions and view.exposure_known and math.isfinite(view.total_value)
        )
        if blocked:
            logger.info(
                "survival state %s (%s): markets not evaluated",
                survival.state.value,
                "; ".join(survival.reasons),
            )
        no_room_logged = False
        pairs = repo.latest_snapshots_for_open_markets(self.s, self.settings.max_markets_per_cycle)
        for market_row, snap_row in pairs:
            market, snap = to_market(market_row), to_snapshot(snap_row)
            if snap.observed_at > self.now or check_eligibility(
                market, snap, self.now, self.settings.eligibility
            ):
                report.ineligible += 1
                continue
            # The key names the strategy that evaluated (VALUE_HUNTER), NO_TRADE outcomes too.
            key = idempotency_key(self.sim_id, market.id, snap_row.id, self.strategy.name.value)
            if self.key_exists(key):
                report.evaluated += 1
                report.skipped_duplicate += 1
                continue
            if blocked:
                report.skipped_by_survival += 1
                continue
            if self.risk.capacity(view) < self.settings.risk.min_stake:
                if not no_room_logged:
                    logger.info("no capacity left for a minimum stake: remaining markets skipped")
                    no_room_logged = True
                report.skipped_no_capacity += 1
                continue
            report.evaluated += 1
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
        # Defaults describe a NO_TRADE; a proposal overrides the trade fields. Unknown
        # values stay None (never 0): the executor fills EV and price from a simulated fill.
        draft = DecisionDraft(
            market_id=market.id,
            snapshot_id=snapshot_id,
            created_at=self.now,
            model_version=estimate.model_version,
            strategy=self.strategy.name.value,
            market_probability=snap.probability,
            estimated_probability=estimate.probability,
            conservative_probability=None,
            uncertainty=estimate.uncertainty,
            expected_value=None,
            ev_per_mana=None,
            action=Action.NO_TRADE.value,
            stake=0.0,
            shares=0.0,
            avg_price=None,
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
    else:
        model = BaseRateShrinkage.fit(
            repo.resolved_binary_outcomes(session, before=now), settings.prediction
        )
        report.model_version = model.version
        view = c.evaluate_markets(report, model, view, survival)

    c.portfolio.record_snapshot(view, c.peak(view), survival, now)
    return report


def evaluation_for_simulation(session: Session, simulation_id: str) -> EvaluationReport:
    """Score the model and the market price on markets that resolved after our first decision."""
    scored = [
        ScoredForecast(market_probability=q, estimated_probability=p, outcome=res == "YES")
        for q, p, res in repo.resolved_decision_scores(session, simulation_id)
    ]
    return evaluate(scored)


MANUAL_STRATEGY = "MANUAL"


class ManualOrderError(Exception):
    """A manual order that cannot even be evaluated (nothing is journaled).

    ``kind`` is one of ``unknown_market``, ``no_simulation``, ``simulation_dead``,
    ``ineligible``. A risk refusal is not an error: it is a journaled REJECTED decision.
    """

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class ManualOrder:
    """A paper order from a person or from Claude, with the actor's own P(YES).

    The stated probability is penalised by the baseline's uncertainty, exactly like
    Ambre's own estimate, and the order goes through the same risk engine, so it can be
    capped or refused. ``request_id`` makes a repeated submission idempotent.
    """

    market_id: str
    side: Side
    probability: float
    stake: float
    actor: Actor
    request_id: str
    note: str | None = None


def manual_order(session: Session, settings: Settings, now: datetime, order: ManualOrder) -> str:
    """Evaluate and, if the risk engine approves, book a manual PAPER order. Returns the
    decision id. The caller commits. Never calls Manifold."""
    if order.actor is Actor.AMBRE:
        raise ValueError("manual orders are taken by USER or CLAUDE, never AMBRE")
    mode = ExecutionGate(settings).check()
    sim = repo.get_current_simulation(session)
    if sim is None:
        raise ManualOrderError("no_simulation", "no active simulation; run `ambre cycle`")
    if sim.status == SimulationStatus.DEAD.value:
        raise ManualOrderError("simulation_dead", f"simulation is DEAD ({sim.end_reason})")
    market_row = repo.get_market_detail(session, order.market_id)
    snap_row = repo.latest_snapshot_for_market(session, order.market_id)
    if market_row is None or snap_row is None:
        raise ManualOrderError("unknown_market", f"unknown market {order.market_id}")
    market, snap = to_market(market_row), to_snapshot(snap_row)
    reasons = check_eligibility(market, snap, now, settings.eligibility)
    if snap.observed_at > now:
        reasons.append("latest snapshot observed after now")
    if reasons:
        raise ManualOrderError("ineligible", "; ".join(reasons))

    c = _Cycle(session, settings, now, mode, sim.id)
    view = c.view()
    survival = c.survival(view, sim.initial_capital)
    model = BaseRateShrinkage.fit(
        repo.resolved_binary_outcomes(session, before=now), settings.prediction
    )
    estimate = model.predict(extract_features(market, snap, now))
    p, u = order.probability, estimate.uncertainty
    p_cons = p - u if order.side is Side.YES else p + u
    p_cons = min(max(p_cons, P_CONS_MIN), P_CONS_MAX)
    reasons = [
        f"manual {order.side.value} by {order.actor.value}",
        f"stated p={p:g}",
        f"q={snap.probability:g}",
        f"u={u:g}",
    ]
    if order.note:
        reasons.append(f"note: {order.note}")
    proposal = Proposal(
        market_id=market.id,
        side=order.side,
        stake=order.stake,
        conservative_probability=p_cons,
        estimate=estimate,
        reasons=reasons,
        invalidation=[],
    )
    verdict, fill_value = c.risk.evaluate(proposal, snap, view, survival)
    draft = DecisionDraft(
        market_id=market.id,
        snapshot_id=snap_row.id,
        created_at=now,
        model_version=f"manual+{estimate.model_version}",
        strategy=MANUAL_STRATEGY,
        market_probability=snap.probability,
        estimated_probability=p,
        conservative_probability=p_cons,
        uncertainty=u,
        expected_value=None,
        ev_per_mana=None,
        action=order.side.value,
        stake=verdict.stake if verdict.approved else proposal.stake,
        shares=0.0,
        avg_price=None,
        reasons=reasons,
        invalidation=[],
        source_ids=[snap.source_id],
        survival_state=survival.state.value,
        risk_reasons=[],
        idempotency_key=f"{sim.id}:{market.id}:{snap_row.id}:{MANUAL_STRATEGY}:{order.request_id}",
        actor=order.actor.value,
    )
    return c.executor.execute(draft, verdict, fill_value)

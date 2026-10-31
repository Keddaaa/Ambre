"""Virtual portfolio: ledger-based cash, positions, valuation, snapshots and settlement.

Functions here flush but never commit: the caller owns the transaction. Every
timestamp comes from a parameter, never from the wall clock.
"""

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ambre.domain.models import Side, SimulationStatus, SurvivalAssessment
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.orm import (
    DecisionRow,
    LedgerRow,
    MarketRow,
    PortfolioSnapshotRow,
    PositionRow,
    SimulationRow,
)
from ambre.risk_engine.risk import PortfolioView

logger = logging.getLogger(__name__)

OPEN = "OPEN"
SETTLED = "SETTLED"


@dataclass(frozen=True)
class SettlementReport:
    settled: int
    refunded: int
    unknown: list[str] = field(default_factory=list)


def _valid_probability(x: float | None) -> bool:
    return x is not None and math.isfinite(x) and 0.0 <= x <= 1.0


class PortfolioService:
    def __init__(self, session: Session, simulation_id: str) -> None:
        self.session = session
        self.simulation_id = simulation_id

    def cash(self) -> float:
        """Cash is the sum of the signed ledger amounts."""
        total = self.session.scalar(
            select(func.coalesce(func.sum(LedgerRow.amount), 0.0)).where(
                LedgerRow.simulation_id == self.simulation_id
            )
        )
        return float(total or 0.0)

    def open_positions(self) -> list[PositionRow]:
        return list(
            self.session.scalars(
                select(PositionRow)
                .where(
                    PositionRow.simulation_id == self.simulation_id,
                    PositionRow.status == OPEN,
                )
                .order_by(PositionRow.id)
            )
        )

    def view(self, latest_prob: Mapping[str, float]) -> PortfolioView:
        """Mark the portfolio to market with ``latest_prob`` (market id -> P(YES)).

        A position is worth ``shares * price of its side`` (latent, unrealised).
        If an open position has no valid price, ``exposure_known`` is False and
        ``total_value`` is cash plus the positions that do have a price (a lower
        bound for the portfolio value). A database error is logged and yields a
        view with ``exposure_known=False`` and NaN valuations; it never raises.
        """
        try:
            cash = self.cash()
            positions = self.open_positions()
        except SQLAlchemyError:
            logger.exception("portfolio valuation failed for simulation %s", self.simulation_id)
            nan = float("nan")
            return PortfolioView(
                cash=nan,
                total_value=nan,
                open_exposure=nan,
                open_market_ids=frozenset(),
                exposure_known=False,
            )
        known = True
        positions_value = 0.0
        for pos in positions:
            p_yes = latest_prob.get(pos.market_id)
            if p_yes is None or not _valid_probability(p_yes):
                known = False
                continue
            price = p_yes if pos.side == Side.YES.value else 1.0 - p_yes
            positions_value += pos.shares * price
        return PortfolioView(
            cash=cash,
            total_value=cash + positions_value,
            open_exposure=sum(p.cost for p in positions),
            open_market_ids=frozenset(p.market_id for p in positions),
            exposure_known=known,
        )

    def record_snapshot(
        self, view: PortfolioView, peak: float, survival: SurvivalAssessment, now: datetime
    ) -> None:
        """Persist a portfolio snapshot. A non-finite valuation is logged, not stored."""
        if not all(math.isfinite(x) for x in (view.cash, view.total_value, peak)) or peak <= 0:
            logger.warning(
                "portfolio snapshot skipped for simulation %s: invalid valuation "
                "(cash=%s, total_value=%s, peak=%s)",
                self.simulation_id,
                view.cash,
                view.total_value,
                peak,
            )
            return
        self.session.add(
            PortfolioSnapshotRow(
                simulation_id=self.simulation_id,
                at=now,
                cash=view.cash,
                positions_value=view.total_value - view.cash,
                total_value=view.total_value,
                peak_value=peak,
                drawdown=max(0.0, 1.0 - view.total_value / peak),
                survival_state=survival.state.value,
                survival_reasons=list(survival.reasons),
            )
        )
        self.session.flush()

    def peak_value(self) -> float:
        """Historical maximum portfolio value, initial capital included."""
        sim = self.session.get(SimulationRow, self.simulation_id)
        if sim is None:
            raise ValueError(f"unknown simulation {self.simulation_id}")
        best = self.session.scalar(
            select(func.max(PortfolioSnapshotRow.total_value)).where(
                PortfolioSnapshotRow.simulation_id == self.simulation_id
            )
        )
        return max(sim.initial_capital, best if best is not None else sim.initial_capital)

    def settle_resolved(self, now: datetime) -> SettlementReport:
        """Settle the OPEN positions of resolved markets, atomically.

        YES/NO pay ``shares`` to the winning side and 0 otherwise; MKT pays
        ``shares * rp`` (YES) or ``shares * (1 - rp)`` (NO); CANCEL refunds the
        cost. Any other resolution (or MKT without a valid probability) leaves the
        position open and reports the market in ``unknown``. Only the decision's
        ``outcome``, ``payout`` and ``settled_at`` are written.
        """
        settled = refunded = 0
        unknown: list[str] = []
        rows = self.session.execute(
            select(PositionRow, MarketRow)
            .join(MarketRow, MarketRow.id == PositionRow.market_id)
            .where(
                PositionRow.simulation_id == self.simulation_id,
                PositionRow.status == OPEN,
                MarketRow.is_resolved.is_(True),
            )
            .order_by(PositionRow.id)
        ).all()
        with self.session.begin_nested():
            for pos, market in rows:
                resolution = market.resolution
                payout = _payout(pos, resolution, market.resolution_probability)
                if payout is None or resolution is None:
                    logger.warning(
                        "market %s resolved with unsupported resolution %r (rp=%r): "
                        "position left open",
                        market.id,
                        resolution,
                        market.resolution_probability,
                    )
                    unknown.append(market.id)
                    continue
                kind = "REFUND" if resolution == "CANCEL" else "SETTLE"
                self.session.add(
                    LedgerRow(
                        simulation_id=self.simulation_id,
                        at=now,
                        kind=kind,
                        amount=payout,
                        decision_id=pos.decision_id,
                        market_id=pos.market_id,
                        note=f"resolution {resolution}",
                    )
                )
                pos.status = SETTLED
                pos.payout = payout
                pos.settled_at = now
                if pos.decision_id is not None:
                    # Targeted UPDATE: prediction columns are never part of the statement.
                    self.session.execute(
                        update(DecisionRow)
                        .where(DecisionRow.decision_id == pos.decision_id)
                        .values(outcome=resolution, payout=payout, settled_at=now)
                        .execution_options(synchronize_session="fetch")
                    )
                if kind == "REFUND":
                    refunded += 1
                else:
                    settled += 1
            self.session.flush()
        return SettlementReport(settled=settled, refunded=refunded, unknown=unknown)


def _payout(pos: PositionRow, resolution: str | None, rp: float | None) -> float | None:
    """Cash returned by a resolved position, or None when the resolution is unsupported."""
    if resolution in (Side.YES.value, Side.NO.value):
        return pos.shares if pos.side == resolution else 0.0
    if resolution == "MKT":
        if rp is None or not _valid_probability(rp):
            return None
        return pos.shares * (rp if pos.side == Side.YES.value else 1.0 - rp)
    if resolution == "CANCEL":
        return pos.cost
    return None


def create_simulation(session: Session, name: str, initial_capital: float, now: datetime) -> str:
    """Archive any ACTIVE simulation (rows kept), create a new one and fund it (ledger INIT)."""
    if not (math.isfinite(initial_capital) and initial_capital > 0):
        raise ValueError(f"initial_capital must be finite and > 0, got {initial_capital}")
    with session.begin_nested():
        active = session.scalars(
            select(SimulationRow).where(SimulationRow.status == SimulationStatus.ACTIVE.value)
        ).all()
        for old in active:
            old.status = SimulationStatus.ARCHIVED.value
            old.ended_at = now
            old.end_reason = f"archived by new simulation {name!r}"
        session.flush()
        sim = repo.create_simulation(session, name, initial_capital, now)
        session.add(
            LedgerRow(
                simulation_id=sim.id,
                at=now,
                kind="INIT",
                amount=initial_capital,
                note="initial capital",
            )
        )
        session.flush()
    return sim.id


def mark_simulation_dead(session: Session, sim_id: str, reason: str, now: datetime) -> None:
    """Mark an ACTIVE simulation DEAD (history kept). Already DEAD is a no-op."""
    sim = session.get(SimulationRow, sim_id)
    if sim is None:
        raise ValueError(f"unknown simulation {sim_id}")
    if sim.status == SimulationStatus.DEAD.value:
        return
    if sim.status != SimulationStatus.ACTIVE.value:
        raise ValueError(f"simulation {sim_id} is {sim.status}, only ACTIVE can die")
    sim.status = SimulationStatus.DEAD.value
    sim.ended_at = now
    sim.end_reason = reason
    session.flush()

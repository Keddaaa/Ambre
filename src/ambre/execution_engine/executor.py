"""Execution gate and paper executor. No code here ever calls a Manifold write endpoint."""

import math
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ambre.config.settings import Settings
from ambre.domain.models import Action, ExecutionMode, ExecutionStatus, RiskVerdict
from ambre.infrastructure.orm import DecisionRow, LedgerRow, PositionRow
from ambre.portfolio.service import OPEN, PortfolioService
from ambre.value_engine.value import FillValue

MANIFOLD_NOT_AVAILABLE = "Manifold order execution is not implemented in v0.1"


class ExecutionModeNotAvailable(RuntimeError):
    pass


class ExecutionGate:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def check(self) -> ExecutionMode:
        """Return the active mode. MANIFOLD_MANA is refused whatever enable_manifold_orders says."""
        mode = self.settings.execution_mode
        if mode is ExecutionMode.MANIFOLD_MANA:
            raise ExecutionModeNotAvailable(MANIFOLD_NOT_AVAILABLE)
        return mode


@dataclass(frozen=True)
class DecisionDraft:
    """Decision columns supplied by the pipeline (status and settlement fields excluded).

    When a ``FillValue`` is passed to ``execute``, its stake, shares, average price,
    expected value and EV per mana replace the draft's; ``risk_reasons`` always
    comes from the verdict.
    """

    market_id: str
    snapshot_id: int
    created_at: datetime
    model_version: str
    strategy: str
    market_probability: float
    estimated_probability: float
    conservative_probability: float
    uncertainty: float
    expected_value: float
    ev_per_mana: float
    action: str
    stake: float
    shares: float
    avg_price: float
    reasons: list[str]
    invalidation: list[str]
    source_ids: list[str]
    survival_state: str
    risk_reasons: list[str]
    idempotency_key: str


class PaperExecutor:
    def __init__(self, session: Session, simulation_id: str, mode: ExecutionMode) -> None:
        if mode is ExecutionMode.MANIFOLD_MANA:
            raise ExecutionModeNotAvailable(MANIFOLD_NOT_AVAILABLE)
        self.session = session
        self.simulation_id = simulation_id
        self.mode = mode

    def _existing(self, key: str) -> str | None:
        return self.session.scalar(
            select(DecisionRow.decision_id).where(DecisionRow.idempotency_key == key)
        )

    def execute(
        self, decision_fields: DecisionDraft, verdict: RiskVerdict, fill_value: FillValue | None
    ) -> str:
        """Record a decision and, for an approved PAPER trade, debit the ledger and open
        the position. Returns the decision id.

        A known idempotency key returns the existing decision id without any write.
        All writes of one call happen inside a SAVEPOINT: on failure none remain.
        The caller owns the commit.
        """
        d = decision_fields
        existing = self._existing(d.idempotency_key)
        if existing is not None:
            return existing

        is_trade = d.action != Action.NO_TRADE.value
        if verdict.approved and not is_trade:
            raise ValueError("an approved verdict cannot come with a NO_TRADE action")
        if not is_trade:
            status = ExecutionStatus.NOT_EXECUTED
        elif not verdict.approved:
            status = ExecutionStatus.REJECTED
        elif self.mode is ExecutionMode.RESEARCH:
            status = ExecutionStatus.NOT_EXECUTED
        else:
            status = ExecutionStatus.SIMULATED

        booking: FillValue | None = None
        if status is ExecutionStatus.SIMULATED:
            if fill_value is None:
                raise ValueError("approved PAPER execution requires a FillValue")
            self._check_fill(d, fill_value)
            booking = fill_value

        values = asdict(d)
        values["risk_reasons"] = list(verdict.reasons)
        if fill_value is not None:
            values.update(
                stake=fill_value.fill.amount,
                shares=fill_value.fill.shares,
                avg_price=fill_value.fill.avg_price,
                expected_value=fill_value.expected_value,
                ev_per_mana=fill_value.ev_per_mana,
            )
        decision_id = str(uuid.uuid4())
        try:
            with self.session.begin_nested():
                self.session.add(
                    DecisionRow(
                        decision_id=decision_id,
                        simulation_id=self.simulation_id,
                        execution_status=status.value,
                        **values,
                    )
                )
                if booking is not None:
                    self._book(d, decision_id, booking)
                self.session.flush()
        except IntegrityError:
            # A concurrent writer may have inserted the same key in the meantime.
            existing = self._existing(d.idempotency_key)
            if existing is not None:
                return existing
            raise
        return decision_id

    def _check_fill(self, d: DecisionDraft, fv: FillValue) -> None:
        if fv.fill.side.value != d.action:
            raise ValueError(f"fill side {fv.fill.side.value} does not match action {d.action}")
        if not (math.isfinite(fv.cost) and fv.cost > 0 and fv.fill.shares > 0):
            raise ValueError(f"invalid fill: cost={fv.cost}, shares={fv.fill.shares}")
        cash = PortfolioService(self.session, self.simulation_id).cash()
        if fv.cost > cash + 1e-9 * max(1.0, abs(cash)):
            raise ValueError(f"fill cost {fv.cost:.4f} exceeds cash {cash:.4f}")

    def _book(self, d: DecisionDraft, decision_id: str, fv: FillValue) -> None:
        self.session.add(
            LedgerRow(
                simulation_id=self.simulation_id,
                at=d.created_at,
                kind="BET",
                amount=-fv.cost,
                decision_id=decision_id,
                market_id=d.market_id,
                note=f"paper {d.action} {fv.fill.shares:.6f} shares",
            )
        )
        self.session.add(
            PositionRow(
                simulation_id=self.simulation_id,
                market_id=d.market_id,
                decision_id=decision_id,
                side=fv.fill.side.value,
                shares=fv.fill.shares,
                cost=fv.cost,
                opened_at=d.created_at,
                status=OPEN,
            )
        )

"""Query-only repositories. They flush but never commit: callers own the transaction."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.orm import Session

from ambre.domain.models import BinaryMarket, MarketSnapshot, SimulationStatus
from ambre.evaluation_engine.backtest import BacktestMarket
from ambre.evaluation_engine.forward import ForwardObservation
from ambre.evaluation_engine.historical import HistoricalMarket
from ambre.evaluation_engine.history import PriceHistory, PricePoint
from ambre.infrastructure.orm import (
    ControlStateRow,
    DecisionRow,
    IgnoredMarketRow,
    MarketRow,
    PortfolioSnapshotRow,
    PositionRow,
    PriceHistoryStateRow,
    PricePointRow,
    SimulationRow,
    SnapshotRow,
    SyncStateRow,
)

CONTROL_KEY = "global"


def upsert_market(
    session: Session, market: BinaryMarket, raw: dict[str, Any], observed_at: datetime
) -> MarketRow:
    values = market.model_dump()
    row = session.get(MarketRow, market.id)
    if row is None:
        row = MarketRow(**values, raw=raw, first_seen_at=observed_at, last_observed_at=observed_at)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.raw = raw
        row.last_observed_at = observed_at
    session.flush()
    return row


def insert_snapshot_if_new(
    session: Session, snap: MarketSnapshot, ingested_at: datetime
) -> int | None:
    if snap.event_at is not None:
        exists = session.scalar(
            select(SnapshotRow.id).where(
                SnapshotRow.market_id == snap.market_id, SnapshotRow.event_at == snap.event_at
            )
        )
        if exists is not None:
            return None
    row = SnapshotRow(**snap.model_dump(), ingested_at=ingested_at)
    session.add(row)
    session.flush()
    return row.id


def upsert_ignored(
    session: Session, market_id: str, outcome_type: str, reason: str, seen_at: datetime
) -> IgnoredMarketRow:
    row = session.get(IgnoredMarketRow, market_id)
    if row is None:
        row = IgnoredMarketRow(
            market_id=market_id, outcome_type=outcome_type, reason=reason, last_seen_at=seen_at
        )
        session.add(row)
    else:
        row.outcome_type = outcome_type
        row.reason = reason
        row.last_seen_at = seen_at
    session.flush()
    return row


def get_sync_state(session: Session, key: str) -> SyncStateRow | None:
    return session.get(SyncStateRow, key)


def save_sync_state(
    session: Session,
    key: str,
    *,
    high_watermark_ms: int | None,
    last_success_at: datetime | None,
    last_error: str | None,
    updated_at: datetime,
    resume_cursor: str | None = None,
    pending_watermark_ms: int | None = None,
) -> SyncStateRow:
    row = session.get(SyncStateRow, key)
    if row is None:
        row = SyncStateRow(key=key)
        session.add(row)
    row.high_watermark_ms = high_watermark_ms
    row.last_success_at = last_success_at
    row.last_error = last_error
    row.resume_cursor = resume_cursor
    row.pending_watermark_ms = pending_watermark_ms
    row.updated_at = updated_at
    session.flush()
    return row


def latest_snapshot_for_market(session: Session, market_id: str) -> SnapshotRow | None:
    return session.scalars(
        select(SnapshotRow)
        .where(SnapshotRow.market_id == market_id)
        .order_by(SnapshotRow.observed_at.desc(), SnapshotRow.id.desc())
        .limit(1)
    ).first()


def latest_snapshots_for_open_markets(
    session: Session, limit: int
) -> list[tuple[MarketRow, SnapshotRow]]:
    latest = (
        select(SnapshotRow.market_id, func.max(SnapshotRow.observed_at).label("max_obs"))
        .group_by(SnapshotRow.market_id)
        .subquery()
    )
    # Ties on observed_at are possible; keep the highest id per (market, observed_at).
    pick = (
        select(func.max(SnapshotRow.id))
        .join(
            latest,
            (SnapshotRow.market_id == latest.c.market_id)
            & (SnapshotRow.observed_at == latest.c.max_obs),
        )
        .group_by(SnapshotRow.market_id)
    )
    stmt = (
        select(MarketRow, SnapshotRow)
        .join(SnapshotRow, SnapshotRow.market_id == MarketRow.id)
        .where(MarketRow.is_resolved.is_(False), SnapshotRow.id.in_(pick))
        .order_by(MarketRow.last_observed_at.desc(), MarketRow.id)
        .limit(limit)
    )
    return [(m, s) for m, s in session.execute(stmt).all()]


def resolved_binary_outcomes(session: Session, before: datetime) -> list[str]:
    stmt = select(MarketRow.resolution).where(
        MarketRow.resolution.in_(("YES", "NO")), MarketRow.resolution_at < before
    )
    return [r for r in session.scalars(stmt) if r is not None]


def get_active_simulation(session: Session) -> SimulationRow | None:
    return session.scalars(
        select(SimulationRow)
        .where(SimulationRow.status == SimulationStatus.ACTIVE.value)
        .order_by(SimulationRow.created_at.desc())
        .limit(1)
    ).first()


def create_simulation(
    session: Session, name: str, initial_capital: float, created_at: datetime
) -> SimulationRow:
    row = SimulationRow(
        id=str(uuid.uuid4()),
        name=name,
        initial_capital=initial_capital,
        status=SimulationStatus.ACTIVE.value,
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    return row


def get_control(session: Session) -> ControlStateRow | None:
    return session.get(ControlStateRow, CONTROL_KEY)


def set_control(
    session: Session, *, halted: bool, reason: str | None, updated_at: datetime
) -> ControlStateRow:
    row = session.get(ControlStateRow, CONTROL_KEY)
    if row is None:
        row = ControlStateRow(key=CONTROL_KEY)
        session.add(row)
    row.halted = halted
    row.reason = reason
    row.updated_at = updated_at
    session.flush()
    return row


def list_markets(session: Session, *, limit: int, offset: int = 0) -> list[MarketRow]:
    return list(
        session.scalars(
            select(MarketRow)
            .order_by(MarketRow.last_observed_at.desc(), MarketRow.id)
            .limit(limit)
            .offset(offset)
        )
    )


def get_market_detail(session: Session, market_id: str) -> MarketRow | None:
    return session.get(MarketRow, market_id)


def list_decisions(
    session: Session, simulation_id: str, *, limit: int, offset: int = 0
) -> list[DecisionRow]:
    return list(
        session.scalars(
            select(DecisionRow)
            .where(DecisionRow.simulation_id == simulation_id)
            .order_by(DecisionRow.created_at.desc(), DecisionRow.decision_id)
            .limit(limit)
            .offset(offset)
        )
    )


def list_portfolio_snapshots(
    session: Session, simulation_id: str, *, limit: int | None = None
) -> list[PortfolioSnapshotRow]:
    stmt = (
        select(PortfolioSnapshotRow)
        .where(PortfolioSnapshotRow.simulation_id == simulation_id)
        .order_by(PortfolioSnapshotRow.at, PortfolioSnapshotRow.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def get_current_simulation(session: Session) -> SimulationRow | None:
    """Most recent simulation that is ACTIVE or DEAD (ARCHIVED ones are never current)."""
    return session.scalars(
        select(SimulationRow)
        .where(
            SimulationRow.status.in_((SimulationStatus.ACTIVE.value, SimulationStatus.DEAD.value))
        )
        .order_by(SimulationRow.created_at.desc())
        .limit(1)
    ).first()


def list_markets_filtered(
    session: Session, *, status: str, q: str | None, limit: int, offset: int
) -> tuple[list[MarketRow], int]:
    """Markets filtered by resolution status ('open'|'resolved'|'all') and a
    case-insensitive substring of the question, with the unpaginated total."""
    conditions: list[ColumnElement[bool]] = []
    if status == "open":
        conditions.append(MarketRow.is_resolved.is_(False))
    elif status == "resolved":
        conditions.append(MarketRow.is_resolved.is_(True))
    if q:
        conditions.append(func.lower(MarketRow.question).contains(q.lower(), autoescape=True))
    total = session.scalar(select(func.count()).select_from(MarketRow).where(*conditions)) or 0
    rows = session.scalars(
        select(MarketRow)
        .where(*conditions)
        .order_by(MarketRow.last_observed_at.desc(), MarketRow.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def list_snapshots_for_market(session: Session, market_id: str) -> list[SnapshotRow]:
    return list(
        session.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.market_id == market_id)
            .order_by(SnapshotRow.observed_at, SnapshotRow.id)
        )
    )


def latest_decisions_by_market(
    session: Session, simulation_id: str, market_ids: list[str]
) -> dict[str, DecisionRow]:
    """Most recent decision per market (within one simulation) for the given markets."""
    if not market_ids:
        return {}
    rows = session.scalars(
        select(DecisionRow)
        .where(DecisionRow.simulation_id == simulation_id, DecisionRow.market_id.in_(market_ids))
        .order_by(DecisionRow.created_at, DecisionRow.decision_id)
    )
    return {d.market_id: d for d in rows}  # ascending order: the last write wins = newest


def list_decisions_filtered(
    session: Session,
    simulation_id: str,
    *,
    action: str | None = None,
    market_id: str | None = None,
    limit: int,
    offset: int = 0,
) -> tuple[list[DecisionRow], int]:
    conditions = [DecisionRow.simulation_id == simulation_id]
    if action is not None:
        conditions.append(DecisionRow.action == action)
    if market_id is not None:
        conditions.append(DecisionRow.market_id == market_id)
    total = session.scalar(select(func.count()).select_from(DecisionRow).where(*conditions)) or 0
    rows = session.scalars(
        select(DecisionRow)
        .where(*conditions)
        .order_by(DecisionRow.created_at.desc(), DecisionRow.decision_id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def count_decisions(session: Session, simulation_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(DecisionRow)
            .where(DecisionRow.simulation_id == simulation_id)
        )
        or 0
    )


def latest_portfolio_snapshot(session: Session, simulation_id: str) -> PortfolioSnapshotRow | None:
    return session.scalars(
        select(PortfolioSnapshotRow)
        .where(PortfolioSnapshotRow.simulation_id == simulation_id)
        .order_by(PortfolioSnapshotRow.at.desc(), PortfolioSnapshotRow.id.desc())
        .limit(1)
    ).first()


def bets_of_simulation(
    session: Session, simulation_id: str
) -> list[tuple[PositionRow, MarketRow, DecisionRow | None]]:
    """Every position of a simulation with its market and the decision that opened it."""
    rows = session.execute(
        select(PositionRow, MarketRow, DecisionRow)
        .join(MarketRow, MarketRow.id == PositionRow.market_id)
        .outerjoin(DecisionRow, DecisionRow.decision_id == PositionRow.decision_id)
        .where(PositionRow.simulation_id == simulation_id)
        .order_by(PositionRow.id)
    ).all()
    return [(pos, market, decision) for pos, market, decision in rows]


def decision_status_counts(session: Session, simulation_id: str) -> dict[str, int]:
    """Number of journaled decisions per execution status."""
    rows = session.execute(
        select(DecisionRow.execution_status, func.count())
        .where(DecisionRow.simulation_id == simulation_id)
        .group_by(DecisionRow.execution_status)
    ).all()
    return {status: count for status, count in rows}


def resolved_decision_scores(
    session: Session, simulation_id: str
) -> list[tuple[float, float, str]]:
    """First decision per market that resolved YES/NO strictly after the decision was made.

    Returns ``(market_probability, estimated_probability, resolution)``. Markets without a
    known ``resolution_at`` are excluded: without it, a leak cannot be ruled out.
    """
    stmt = (
        select(DecisionRow, MarketRow.resolution)
        .join(MarketRow, MarketRow.id == DecisionRow.market_id)
        .where(
            DecisionRow.simulation_id == simulation_id,
            MarketRow.resolution.in_(("YES", "NO")),
            MarketRow.resolution_at.is_not(None),
            DecisionRow.created_at < MarketRow.resolution_at,
        )
        .order_by(DecisionRow.created_at, DecisionRow.decision_id)
    )
    first: dict[str, tuple[float, float, str]] = {}
    for decision, resolution in session.execute(stmt).all():
        first.setdefault(
            decision.market_id,
            (decision.market_probability, decision.estimated_probability, str(resolution)),
        )
    return list(first.values())


def backtest_markets(session: Session) -> list[BacktestMarket]:
    """YES/NO markets with a known resolution date, seen at their first snapshot.

    The decision instant is the earliest ``observed_at`` and the price is that
    snapshot's probability. Markets without a snapshot, unresolved ones, other
    resolutions (CANCEL, MKT, ...) and undated resolutions are left out.
    """
    stmt = (
        select(SnapshotRow.market_id, SnapshotRow.observed_at, SnapshotRow.probability)
        .join(MarketRow, MarketRow.id == SnapshotRow.market_id)
        .where(MarketRow.resolution.in_(("YES", "NO")), MarketRow.resolution_at.is_not(None))
        .order_by(SnapshotRow.market_id, SnapshotRow.observed_at, SnapshotRow.id)
    )
    first: dict[str, tuple[datetime, float]] = {}
    for market_id, observed_at, probability in session.execute(stmt).all():
        first.setdefault(market_id, (observed_at, probability))
    markets = {
        m.id: m for m in session.scalars(select(MarketRow).where(MarketRow.id.in_(list(first))))
    }
    return [
        BacktestMarket(
            market_id=market_id,
            decision_at=decision_at,
            market_probability=probability,
            resolution_at=markets[market_id].resolution_at,
            outcome=markets[market_id].resolution == "YES",
        )
        for market_id, (decision_at, probability) in sorted(first.items())
    ]


def markets_needing_history(session: Session, limit: int) -> list[str]:
    """Resolved YES/NO markets with a known resolution date and no stored history yet, the most
    recently resolved first. A market whose fetch was incomplete is not asked again."""
    stmt = (
        select(MarketRow.id)
        .outerjoin(PriceHistoryStateRow, PriceHistoryStateRow.market_id == MarketRow.id)
        .where(
            MarketRow.resolution.in_(("YES", "NO")),
            MarketRow.resolution_at.is_not(None),
            PriceHistoryStateRow.market_id.is_(None),
        )
        .order_by(MarketRow.resolution_at.desc(), MarketRow.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def save_price_history(
    session: Session, history: PriceHistory, bet_count: int, now: datetime
) -> None:
    """Store a fetched history. Re-saving a market replaces its points: the call is idempotent."""
    session.execute(delete(PricePointRow).where(PricePointRow.market_id == history.market_id))
    existing = session.get(PriceHistoryStateRow, history.market_id)
    if existing is not None:
        session.delete(existing)
    session.flush()
    session.add(
        PriceHistoryStateRow(
            market_id=history.market_id,
            complete=history.complete,
            bet_count=bet_count,
            dropped=history.dropped,
            initial_probability=history.initial_probability,
            fetched_at=now,
        )
    )
    session.add_all(
        PricePointRow(
            market_id=history.market_id, bet_id=p.bet_id, at=p.at, probability=p.probability
        )
        for p in history.points
    )
    session.flush()


def historical_markets(session: Session) -> list[HistoricalMarket]:
    """Resolved YES/NO markets with a stored history, ready for the point-in-time evaluation."""
    rows = session.execute(
        select(MarketRow, PriceHistoryStateRow)
        .join(PriceHistoryStateRow, PriceHistoryStateRow.market_id == MarketRow.id)
        .where(MarketRow.resolution.in_(("YES", "NO")), MarketRow.resolution_at.is_not(None))
        .order_by(MarketRow.resolution_at, MarketRow.id)
    ).all()
    points: dict[str, list[PricePoint]] = {}
    for p in session.scalars(
        select(PricePointRow).order_by(PricePointRow.market_id, PricePointRow.at)
    ):
        points.setdefault(p.market_id, []).append(PricePoint(p.at, p.probability, p.bet_id))
    out: list[HistoricalMarket] = []
    for market, state in rows:
        assert market.resolution_at is not None
        out.append(
            HistoricalMarket(
                market_id=market.id,
                question=market.question,
                created_at=market.created_at,
                close_at=market.close_at,
                resolution_at=market.resolution_at,
                outcome=market.resolution == "YES",
                history=PriceHistory(
                    market_id=market.id,
                    initial_probability=state.initial_probability,
                    points=tuple(points.get(market.id, [])),
                    complete=state.complete,
                    dropped=state.dropped,
                ),
            )
        )
    return out


def forward_observations(
    session: Session, min_age_days: float, max_age_days: float
) -> tuple[list[ForwardObservation], dict[str, int]]:
    """The forward-test sample (ADR-019): for each market, the model's FIRST decision taken while
    the market's age was inside the window, kept only if the market resolved YES/NO strictly after
    that decision. Decisions of any other model are ignored. Also the counts behind it."""
    stmt = (
        select(DecisionRow, MarketRow)
        .join(MarketRow, MarketRow.id == DecisionRow.market_id)
        .where(DecisionRow.model_version.like("recalibrated-price%"))
        .order_by(DecisionRow.created_at, DecisionRow.decision_id)
    )
    first: dict[str, tuple[DecisionRow, MarketRow]] = {}
    for decision, market in session.execute(stmt).all():
        age_days = (decision.created_at - market.created_at).total_seconds() / 86_400.0
        if min_age_days <= age_days <= max_age_days and market.id not in first:
            first[market.id] = (decision, market)
    observations = [
        ForwardObservation(
            market_id=market.id,
            question=market.question,
            market_probability=decision.market_probability,
            estimated_probability=decision.estimated_probability,
            outcome=market.resolution == "YES",
        )
        for decision, market in first.values()
        if market.resolution in ("YES", "NO")
        and market.resolution_at is not None
        and decision.created_at < market.resolution_at
    ]
    counts = {
        "markets_with_window_decision": len(first),
        "resolved_yes_no": len(observations),
        "pending": sum(1 for _, m in first.values() if m.resolution is None),
    }
    return observations, counts

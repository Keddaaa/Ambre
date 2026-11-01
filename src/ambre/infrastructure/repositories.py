"""Query-only repositories. They flush but never commit: callers own the transaction."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from ambre.domain.models import BinaryMarket, MarketSnapshot, SimulationStatus
from ambre.infrastructure.orm import (
    ControlStateRow,
    DecisionRow,
    IgnoredMarketRow,
    MarketRow,
    PortfolioSnapshotRow,
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

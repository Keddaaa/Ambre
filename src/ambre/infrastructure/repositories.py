"""Query-only repositories. They flush but never commit: callers own the transaction."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
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

"""SQLAlchemy tables. Portable types only (JSON, String, Float, Integer, Boolean, UTCDateTime)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ambre.infrastructure.db import UTCDateTime


class Base(DeclarativeBase):
    pass


class MarketRow(Base):
    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    question: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    creator_id: Mapped[str] = mapped_column(String)
    creator_username: Mapped[str] = mapped_column(String)
    mechanism: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    close_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    token: Mapped[str | None] = mapped_column(String, nullable=True)
    group_slugs: Mapped[list[str]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class IgnoredMarketRow(Base):
    __tablename__ = "ignored_markets"

    market_id: Mapped[str] = mapped_column(String, primary_key=True)
    outcome_type: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SnapshotRow(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("market_id", "event_at"),
        Index("ix_market_snapshots_market_id_observed_at", "market_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    probability: Mapped[float] = mapped_column(Float)
    pool_yes: Mapped[float] = mapped_column(Float)
    pool_no: Mapped[float] = mapped_column(Float)
    p: Mapped[float] = mapped_column(Float)
    total_liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    unique_bettor_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    event_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    source_id: Mapped[str] = mapped_column(String)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SyncStateRow(Base):
    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    high_watermark_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    resume_cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_watermark_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SimulationRow(Base):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    initial_capital: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String, nullable=True)


class DecisionRow(Base):
    __tablename__ = "decisions"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String, ForeignKey("simulations.id"), index=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("market_snapshots.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    model_version: Mapped[str] = mapped_column(String)
    strategy: Mapped[str] = mapped_column(String)
    market_probability: Mapped[float] = mapped_column(Float)
    estimated_probability: Mapped[float] = mapped_column(Float)
    conservative_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty: Mapped[float] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_per_mana: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String)
    stake: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    invalidation: Mapped[list[str]] = mapped_column(JSON)
    source_ids: Mapped[list[str]] = mapped_column(JSON)
    survival_state: Mapped[str] = mapped_column(String)
    risk_reasons: Mapped[list[str]] = mapped_column(JSON)
    execution_status: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    actor: Mapped[str] = mapped_column(String, default="AMBRE", server_default="AMBRE")
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    payout: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class PositionRow(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("simulation_id", "market_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(String, ForeignKey("simulations.id"))
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"))
    decision_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("decisions.decision_id"), nullable=True
    )
    side: Mapped[str] = mapped_column(String)
    shares: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String)  # OPEN / SETTLED
    payout: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class LedgerRow(Base):
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(String, ForeignKey("simulations.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    kind: Mapped[str] = mapped_column(String)  # INIT / BET / SETTLE / REFUND
    amount: Mapped[float] = mapped_column(Float)  # signed cash delta
    decision_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("decisions.decision_id"), nullable=True
    )
    market_id: Mapped[str | None] = mapped_column(String, ForeignKey("markets.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)


class PortfolioSnapshotRow(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(String, ForeignKey("simulations.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    cash: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float)
    total_value: Mapped[float] = mapped_column(Float)
    peak_value: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    survival_state: Mapped[str] = mapped_column(String)
    survival_reasons: Mapped[list[str]] = mapped_column(JSON)


class ControlStateRow(Base):
    __tablename__ = "control_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    halted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PriceHistoryStateRow(Base):
    """Whether the bet history of a resolved market was fetched, and how completely.

    An incomplete history (the page cap was reached before the first bet) is kept for the record
    but never used: its early prices would be guessed.
    """

    __tablename__ = "market_price_history"

    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), primary_key=True)
    complete: Mapped[bool] = mapped_column(Boolean)
    bet_count: Mapped[int] = mapped_column(Integer)
    dropped: Mapped[int] = mapped_column(Integer)
    initial_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PricePointRow(Base):
    """The market probability right after one bet (``probAfter``), as Manifold reported it."""

    __tablename__ = "market_price_points"
    __table_args__ = (Index("ix_market_price_points_market_at", "market_id", "at"),)

    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), primary_key=True)
    bet_id: Mapped[str] = mapped_column(String, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    probability: Mapped[float] = mapped_column(Float)


class WorkerLeaseRow(Base):
    """The single lease a worker must hold to run on this database (see ``worker_lease``)."""

    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    holder: Mapped[str] = mapped_column(String)
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WorkerRunRow(Base):
    """One worker run: start, last heartbeat, how it stopped, and the downtime before it."""

    __tablename__ = "worker_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, index=True)
    holder: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    iterations: Mapped[int] = mapped_column(Integer)
    gap_before_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

"""Tables of the Global Intelligence Network. Same base and portable types as the core schema.

Nothing here touches decisions, positions or the ledger: the network reads Manifold data and
writes only its own tables.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ambre.infrastructure.db import UTCDateTime
from ambre.infrastructure.orm import Base


class IntelRequestRow(Base):
    """Research Memory: one row per research decision, including the ones that did not search."""

    __tablename__ = "intel_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    need: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    market_ids: Mapped[list[str]] = mapped_column(JSON)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_units: Mapped[int] = mapped_column(Integer)
    observation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invalid_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Filled retrospectively by the learning phase; None = not assessed, never "useless".
    usefulness: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class IntelObservationRow(Base):
    __tablename__ = "intel_observations"

    observation_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider_id: Mapped[str] = mapped_column(String, index=True)
    domain: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    payload_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    verification_status: Mapped[str] = mapped_column(String)
    source_reliability: Mapped[float | None] = mapped_column(Float, nullable=True)
    revision: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("intel_requests.id"), nullable=True
    )


class IntelMarketLinkRow(Base):
    """Relation between an observation and a market, with provenance and evidence level."""

    __tablename__ = "intel_market_links"
    __table_args__ = (UniqueConstraint("market_id", "observation_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    observation_id: Mapped[str] = mapped_column(
        String, ForeignKey("intel_observations.observation_id")
    )
    relation: Mapped[str] = mapped_column(String)
    criteria_status: Mapped[str] = mapped_column(String)
    evidence_level: Mapped[str] = mapped_column(String)
    usable_for_prediction: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class IntelUsageRow(Base):
    """Every call made to a provider, successful or not: the Budget Controller counts these."""

    __tablename__ = "intel_usage"
    __table_args__ = (Index("ix_intel_usage_provider_at", "provider_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    units: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)


class IntelProviderStateRow(Base):
    __tablename__ = "intel_provider_state"

    provider_id: Mapped[str] = mapped_column(String, primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer)
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class IntelJobRow(Base):
    """A scheduled job. ``next_run_at`` only moves forward when the job really ran (or failed):
    a run skipped because of an emergency stop, a lease or stale data leaves it due."""

    __tablename__ = "intel_jobs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class IntelJobRunRow(Base):
    """Journal of every scheduling decision. ``slot`` is set only for a run that executed, and
    (job, slot) is unique: two runners can never both execute the same time slot."""

    __tablename__ = "intel_job_runs"
    __table_args__ = (UniqueConstraint("job_name", "slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String, ForeignKey("intel_jobs.name"), index=True)
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)

"""global intelligence network tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02 08:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intel_requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("need", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("market_ids", sa.JSON(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("value_score", sa.Float(), nullable=True),
        sa.Column("cost_units", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=True),
        sa.Column("invalid_items", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("usefulness", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("intel_requests") as batch:
        batch.create_index("ix_intel_requests_created_at", ["created_at"])
        batch.create_index("ix_intel_requests_need", ["need"])

    op.create_table(
        "intel_observations",
        sa.Column("observation_id", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_status", sa.String(), nullable=False),
        sa.Column("source_reliability", sa.Float(), nullable=True),
        sa.Column("revision", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["intel_requests.id"]),
        sa.PrimaryKeyConstraint("observation_id"),
    )
    with op.batch_alter_table("intel_observations") as batch:
        batch.create_index("ix_intel_observations_provider_id", ["provider_id"])
        batch.create_index("ix_intel_observations_event_at", ["event_at"])

    op.create_table(
        "intel_market_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.String(), nullable=False),
        sa.Column("observation_id", sa.String(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("criteria_status", sa.String(), nullable=False),
        sa.Column("evidence_level", sa.String(), nullable=False),
        sa.Column("usable_for_prediction", sa.Boolean(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["observation_id"], ["intel_observations.observation_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", "observation_id"),
    )
    with op.batch_alter_table("intel_market_links") as batch:
        batch.create_index("ix_intel_market_links_market_id", ["market_id"])

    op.create_table(
        "intel_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("intel_usage") as batch:
        batch.create_index("ix_intel_usage_provider_at", ["provider_id", "at"])

    op.create_table(
        "intel_provider_state",
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider_id"),
    )


def downgrade() -> None:
    op.drop_table("intel_provider_state")
    op.drop_table("intel_usage")
    op.drop_table("intel_market_links")
    op.drop_table("intel_observations")
    op.drop_table("intel_requests")

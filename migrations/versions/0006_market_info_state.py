"""market information state

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "intel_market_state",
        sa.Column("market_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("plan_status", sa.String(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("estimable", sa.Boolean(), nullable=False),
        sa.Column("topic_key", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("last_outcome", sa.String(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("last_researched_at", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("market_id"),
    )
    with op.batch_alter_table("intel_market_state") as batch:
        batch.create_index("ix_intel_market_state_state", ["state"])
        batch.create_index("ix_intel_market_state_domain", ["domain"])


def downgrade() -> None:
    op.drop_table("intel_market_state")

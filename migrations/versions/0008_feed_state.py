"""news feed polling state

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-02 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "intel_feed_state",
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("etag", sa.String(), nullable=True),
        sa.Column("last_modified", sa.String(), nullable=True),
        sa.Column("last_polled_at", TS, nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("last_item_count", sa.Integer(), nullable=True),
        sa.Column("newest_published_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("provider_id"),
    )


def downgrade() -> None:
    op.drop_table("intel_feed_state")

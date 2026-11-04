"""traced invalidation of market links

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-04 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("intel_market_links") as batch:
        batch.add_column(sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("invalidated_reason", sa.String(), nullable=True))
        batch.add_column(sa.Column("invalidated_by", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("intel_market_links") as batch:
        batch.drop_column("invalidated_by")
        batch.drop_column("invalidated_reason")
        batch.drop_column("invalidated_at")

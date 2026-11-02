"""market price history (rebuilt from bets, for point-in-time evaluation)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "market_price_history",
        sa.Column("market_id", sa.String(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("bet_count", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        sa.Column("initial_probability", sa.Float(), nullable=True),
        sa.Column("fetched_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("market_id"),
    )
    op.create_table(
        "market_price_points",
        sa.Column("market_id", sa.String(), nullable=False),
        sa.Column("bet_id", sa.String(), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("market_id", "bet_id"),
    )
    with op.batch_alter_table("market_price_points") as batch:
        batch.create_index("ix_market_price_points_market_at", ["market_id", "at"])


def downgrade() -> None:
    op.drop_table("market_price_points")
    op.drop_table("market_price_history")

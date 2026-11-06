"""bet-level history of resolved markets

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-05 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_bet_state",
        sa.Column("market_id", sa.String(), sa.ForeignKey("markets.id"), primary_key=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("bet_count", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "market_bets",
        sa.Column("market_id", sa.String(), sa.ForeignKey("markets.id"), primary_key=True),
        sa.Column("bet_id", sa.String(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prob_before", sa.Float(), nullable=False),
        sa.Column("prob_after", sa.Float(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("bettor", sa.String(), nullable=True),
        sa.Column("is_api", sa.Boolean(), nullable=True),
    )
    op.create_index("ix_market_bets_market_at", "market_bets", ["market_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_market_bets_market_at", table_name="market_bets")
    op.drop_table("market_bets")
    op.drop_table("market_bet_state")

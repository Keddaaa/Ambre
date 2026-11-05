"""feature journal

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "intel_feature_journal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.String(), nullable=False),
        sa.Column("as_of", TS, nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("need", sa.String(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("estimable", sa.Boolean(), nullable=False),
        sa.Column("market_probability", sa.Float(), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("intel_feature_journal") as batch:
        batch.create_index("ix_intel_feature_journal_domain", ["domain"])
        batch.create_index("ix_intel_feature_journal_market_as_of", ["market_id", "as_of"])


def downgrade() -> None:
    op.drop_table("intel_feature_journal")

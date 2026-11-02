"""decision actor

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02 05:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows were all taken by the algorithm.
    op.add_column(
        "decisions",
        sa.Column("actor", sa.String(), nullable=False, server_default="AMBRE"),
    )


def downgrade() -> None:
    with op.batch_alter_table("decisions") as batch:
        batch.drop_column("actor")

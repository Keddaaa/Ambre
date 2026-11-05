"""worker lease and run journal

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "worker_lease",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("acquired_at", TS, nullable=False),
        sa.Column("heartbeat_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "worker_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("last_heartbeat_at", TS, nullable=False),
        sa.Column("stopped_at", TS, nullable=True),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False),
        sa.Column("gap_before_seconds", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("worker_runs") as batch:
        batch.create_index("ix_worker_runs_name", ["name"])


def downgrade() -> None:
    op.drop_table("worker_runs")
    op.drop_table("worker_lease")

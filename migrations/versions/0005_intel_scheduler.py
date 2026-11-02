"""research scheduler tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "intel_jobs",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", TS, nullable=False),
        sa.Column("last_run_at", TS, nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("lease_until", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "intel_job_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["job_name"], ["intel_jobs.name"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_name", "slot"),
    )
    with op.batch_alter_table("intel_job_runs") as batch:
        batch.create_index("ix_intel_job_runs_job_name", ["job_name"])


def downgrade() -> None:
    op.drop_table("intel_job_runs")
    op.drop_table("intel_jobs")

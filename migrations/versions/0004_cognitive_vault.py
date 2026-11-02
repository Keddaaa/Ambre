"""cognitive vault tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "cognitive_notes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("evidence_level", sa.String(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("valid_from", TS, nullable=True),
        sa.Column("valid_until", TS, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cognitive_notes") as batch:
        batch.create_index("ix_cognitive_notes_type", ["type"])
        batch.create_index("ix_cognitive_notes_status", ["status"])
        batch.create_index("ix_cognitive_notes_fingerprint", ["fingerprint"])

    op.create_table(
        "cognitive_note_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("note_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("changed_at", TS, nullable=False),
        sa.Column("changed_by", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["note_id"], ["cognitive_notes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", "version"),
    )
    with op.batch_alter_table("cognitive_note_versions") as batch:
        batch.create_index("ix_cognitive_note_versions_note_id", ["note_id"])

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("src_id", sa.String(), nullable=False),
        sa.Column("dst_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("retracted_at", TS, nullable=True),
        sa.ForeignKeyConstraint(["src_id"], ["cognitive_notes.id"]),
        sa.ForeignKeyConstraint(["dst_id"], ["cognitive_notes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("src_id", "dst_id", "type"),
    )
    with op.batch_alter_table("knowledge_edges") as batch:
        batch.create_index("ix_knowledge_edges_src_id", ["src_id"])
        batch.create_index("ix_knowledge_edges_dst_id", ["dst_id"])

    op.create_table(
        "rule_proposals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("source_note_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("rule_proposals") as batch:
        batch.create_index("ix_rule_proposals_domain", ["domain"])
        batch.create_index("ix_rule_proposals_status", ["status"])

    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("changed_at", TS, nullable=False),
        sa.Column("changed_by", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["rule_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "version"),
    )
    with op.batch_alter_table("rule_versions") as batch:
        batch.create_index("ix_rule_versions_rule_id", ["rule_id"])

    op.create_table(
        "cognitive_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cognitive_events") as batch:
        batch.create_index("ix_cognitive_events_at", ["at"])
        batch.create_index("ix_cognitive_events_subject", ["subject_type", "subject_id"])


def downgrade() -> None:
    op.drop_table("cognitive_events")
    op.drop_table("rule_versions")
    op.drop_table("rule_proposals")
    op.drop_table("knowledge_edges")
    op.drop_table("cognitive_note_versions")
    op.drop_table("cognitive_notes")

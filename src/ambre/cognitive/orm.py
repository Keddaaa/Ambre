"""Cognitive Vault tables. Same base and portable types as the rest of the schema.

History tables (`cognitive_note_versions`, `rule_versions`) are append-only by construction:
no code path updates or deletes a row in them. Nothing here references decisions, positions or
the ledger by foreign key, so the vault cannot alter the bet path.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ambre.infrastructure.db import UTCDateTime
from ambre.infrastructure.orm import Base


class CognitiveNoteRow(Base):
    __tablename__ = "cognitive_notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String, index=True)
    evidence_level: Mapped[str] = mapped_column(String)
    context: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class CognitiveNoteVersionRow(Base):
    __tablename__ = "cognitive_note_versions"
    __table_args__ = (UniqueConstraint("note_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String, ForeignKey("cognitive_notes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    changed_by: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)


class KnowledgeEdgeRow(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (UniqueConstraint("src_id", "dst_id", "type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src_id: Mapped[str] = mapped_column(String, ForeignKey("cognitive_notes.id"), index=True)
    dst_id: Mapped[str] = mapped_column(String, ForeignKey("cognitive_notes.id"), index=True)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    retracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RuleProposalRow(Base):
    """Current state of a rule. Every earlier state lives in `rule_versions`."""

    __tablename__ = "rule_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String)
    source_note_ids: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RuleVersionRow(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String, ForeignKey("rule_proposals.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    changed_by: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)


class CognitiveEventRow(Base):
    """Audit trail: who did what to which subject, and why it was refused when it was."""

    __tablename__ = "cognitive_events"
    __table_args__ = (Index("ix_cognitive_events_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    kind: Mapped[str] = mapped_column(String)
    subject_type: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)

"""Cognitive Vault repository: notes, versions, relations, search, export, audit events.

The database is the source of truth. Every write runs in a savepoint (nothing half-written),
history rows are only ever appended, and updates use a compare-and-swap on the version so two
writers can never both win the same version. The caller owns the commit.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ambre.cognitive.markdown import RelationView, render_note
from ambre.cognitive.models import (
    ID_PATTERN,
    EdgeDraft,
    EdgeStatus,
    EvidenceLevel,
    NoteDraft,
    NoteRecord,
    NoteStatus,
    NoteType,
    Provenance,
    RelationType,
    content_fingerprint,
)
from ambre.cognitive.orm import (
    CognitiveEventRow,
    CognitiveNoteRow,
    CognitiveNoteVersionRow,
    KnowledgeEdgeRow,
)
from ambre.domain.models import Actor

MUTABLE_FIELDS = frozenset(
    {
        "title",
        "content",
        "status",
        "evidence_level",
        "context",
        "confidence",
        "valid_from",
        "valid_until",
    }
)


class VaultError(Exception):
    """Base class of every vault refusal."""


class NoteNotFound(VaultError):
    pass


class NoteExists(VaultError):
    pass


class DuplicateNote(VaultError):
    def __init__(self, existing_ids: list[str]) -> None:
        super().__init__(f"same substance already stored: {existing_ids}")
        self.existing_ids = existing_ids


class VersionConflict(VaultError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"expected version {expected}, found {actual}")
        self.expected = expected
        self.actual = actual


class EdgeError(VaultError):
    pass


class EdgeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    src: str
    dst: str
    type: RelationType
    status: EdgeStatus
    version: int
    provenance: Provenance
    created_at: datetime
    retracted_at: datetime | None = None


class EventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    at: datetime
    kind: str
    subject_type: str
    subject_id: str
    actor: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class SearchHit:
    note: NoteRecord
    score: int


# ------------------------------------------------------------------------------ helpers


def _to_record(row: CognitiveNoteRow) -> NoteRecord:
    return NoteRecord(
        note_id=row.id,
        version=row.version,
        title=row.title,
        type=NoteType(row.type),
        content=row.content,
        status=NoteStatus(row.status),
        evidence_level=EvidenceLevel(row.evidence_level),
        context=dict(row.context),
        confidence=row.confidence,
        provenance=Provenance.model_validate(row.provenance),
        created_at=row.created_at,
        updated_at=row.updated_at,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
    )


def _to_edge(row: KnowledgeEdgeRow) -> EdgeRecord:
    return EdgeRecord(
        id=row.id,
        src=row.src_id,
        dst=row.dst_id,
        type=RelationType(row.type),
        status=EdgeStatus(row.status),
        version=row.version,
        provenance=Provenance.model_validate(row.provenance),
        created_at=row.created_at,
        retracted_at=row.retracted_at,
    )


def _version_row(
    record: NoteRecord, *, changed_at: datetime, changed_by: Actor, reason: str | None
) -> CognitiveNoteVersionRow:
    return CognitiveNoteVersionRow(
        note_id=record.note_id,
        version=record.version,
        snapshot=record.model_dump(mode="json"),
        changed_at=changed_at,
        changed_by=changed_by.value,
        reason=reason,
    )


def record_event(
    session: Session,
    kind: str,
    subject_type: str,
    subject_id: str,
    actor: Actor,
    detail: Mapping[str, Any],
    now: datetime,
) -> None:
    session.add(
        CognitiveEventRow(
            at=now,
            kind=kind,
            subject_type=subject_type,
            subject_id=subject_id,
            actor=actor.value,
            detail=dict(detail),
        )
    )


def events_for(session: Session, subject_type: str, subject_id: str) -> list[EventRecord]:
    rows = session.scalars(
        select(CognitiveEventRow)
        .where(
            CognitiveEventRow.subject_type == subject_type,
            CognitiveEventRow.subject_id == subject_id,
        )
        .order_by(CognitiveEventRow.id)
    )
    return [
        EventRecord(
            id=r.id,
            at=r.at,
            kind=r.kind,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            actor=r.actor,
            detail=dict(r.detail),
        )
        for r in rows
    ]


# ------------------------------------------------------------------------------- notes


def find_duplicates(session: Session, draft: NoteDraft) -> list[str]:
    """Ids of stored notes with the same type and normalised title and content."""
    fingerprint = content_fingerprint(draft.type, draft.title, draft.content)
    return list(
        session.scalars(
            select(CognitiveNoteRow.id)
            .where(CognitiveNoteRow.fingerprint == fingerprint)
            .order_by(CognitiveNoteRow.id)
        )
    )


def create_note(
    session: Session, draft: NoteDraft, now: datetime, *, allow_duplicate: bool = False
) -> NoteRecord:
    note_id = draft.note_id or f"note-{uuid.uuid4().hex[:12]}"
    if session.get(CognitiveNoteRow, note_id) is not None:
        raise NoteExists(f"note {note_id} already exists")
    if not allow_duplicate:
        existing = find_duplicates(session, draft)
        if existing:
            raise DuplicateNote(existing)
    record = NoteRecord(
        note_id=note_id,
        version=1,
        title=draft.title,
        type=draft.type,
        content=draft.content,
        status=draft.status,
        evidence_level=draft.evidence_level,
        context=dict(draft.context),
        confidence=draft.confidence,
        provenance=draft.provenance,
        created_at=now,
        updated_at=now,
        valid_from=draft.valid_from,
        valid_until=draft.valid_until,
    )
    with session.begin_nested():
        session.add(
            CognitiveNoteRow(
                id=note_id,
                type=record.type.value,
                status=record.status.value,
                version=1,
                title=record.title,
                content=record.content,
                fingerprint=content_fingerprint(record.type, record.title, record.content),
                evidence_level=record.evidence_level.value,
                context=dict(record.context),
                confidence=record.confidence,
                provenance=record.provenance.model_dump(mode="json"),
                created_at=now,
                updated_at=now,
                valid_from=record.valid_from,
                valid_until=record.valid_until,
            )
        )
        session.flush()
        session.add(
            _version_row(
                record, changed_at=now, changed_by=draft.provenance.created_by, reason="created"
            )
        )
        record_event(
            session,
            "note_created",
            "note",
            note_id,
            draft.provenance.created_by,
            {"type": record.type.value, "source_refs": list(record.provenance.source_refs)},
            now,
        )
        session.flush()
    return record


def get_note(session: Session, note_id: str) -> NoteRecord | None:
    row = session.get(CognitiveNoteRow, note_id)
    return _to_record(row) if row is not None else None


def note_history(session: Session, note_id: str) -> list[NoteRecord]:
    rows = session.scalars(
        select(CognitiveNoteVersionRow)
        .where(CognitiveNoteVersionRow.note_id == note_id)
        .order_by(CognitiveNoteVersionRow.version)
    )
    return [NoteRecord.model_validate(r.snapshot) for r in rows]


def update_note(
    session: Session,
    note_id: str,
    *,
    expected_version: int,
    now: datetime,
    actor: Actor,
    reason: str,
    changes: Mapping[str, Any],
) -> NoteRecord:
    """Write version ``expected_version + 1``. The note's type and id never change."""
    if "type" in changes:
        raise ValueError("a note's type is immutable: a hypothesis cannot become a fact")
    unknown = sorted(set(changes) - MUTABLE_FIELDS)
    if unknown:
        raise ValueError(f"unknown field(s): {unknown}")
    row = session.get(CognitiveNoteRow, note_id)
    if row is None:
        raise NoteNotFound(note_id)
    if row.version != expected_version:
        raise VersionConflict(expected_version, row.version)
    current = _to_record(row)
    merged: dict[str, Any] = {
        "title": current.title,
        "content": current.content,
        "status": current.status,
        "evidence_level": current.evidence_level,
        "context": current.context,
        "confidence": current.confidence,
        "valid_from": current.valid_from,
        "valid_until": current.valid_until,
        **changes,
    }
    # Re-validate the whole note: a change can never bypass the creation rules.
    draft = NoteDraft(type=current.type, provenance=current.provenance, **merged)
    new = current.model_copy(
        update={
            "version": expected_version + 1,
            "title": draft.title,
            "content": draft.content,
            "status": draft.status,
            "evidence_level": draft.evidence_level,
            "context": dict(draft.context),
            "confidence": draft.confidence,
            "valid_from": draft.valid_from,
            "valid_until": draft.valid_until,
            "updated_at": now,
        }
    )
    with session.begin_nested():
        result = cast(
            "CursorResult[Any]",
            session.execute(
                update(CognitiveNoteRow)
                .where(
                    and_(
                        CognitiveNoteRow.id == note_id, CognitiveNoteRow.version == expected_version
                    )
                )
                .values(
                    version=new.version,
                    title=new.title,
                    content=new.content,
                    status=new.status.value,
                    fingerprint=content_fingerprint(new.type, new.title, new.content),
                    evidence_level=new.evidence_level.value,
                    context=dict(new.context),
                    confidence=new.confidence,
                    valid_from=new.valid_from,
                    valid_until=new.valid_until,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            session.expire(row)
            raise VersionConflict(expected_version, session.get(CognitiveNoteRow, note_id).version)  # type: ignore[union-attr]
        session.add(_version_row(new, changed_at=now, changed_by=actor, reason=reason))
        record_event(
            session,
            "note_updated",
            "note",
            note_id,
            actor,
            {"reason": reason, "fields": sorted(changes), "version": new.version},
            now,
        )
        session.flush()
    session.expire(row)
    return new


# ---------------------------------------------------------------------------- relations


def add_edge(session: Session, draft: EdgeDraft, now: datetime) -> EdgeRecord:
    for note_id in (draft.src, draft.dst):
        if session.get(CognitiveNoteRow, note_id) is None:
            raise EdgeError(f"unknown note {note_id}")
    exists = session.scalar(
        select(KnowledgeEdgeRow.id).where(
            KnowledgeEdgeRow.src_id == draft.src,
            KnowledgeEdgeRow.dst_id == draft.dst,
            KnowledgeEdgeRow.type == draft.type.value,
        )
    )
    if exists is not None:
        raise EdgeError(f"relation {draft.type.value} {draft.src} -> {draft.dst} already stored")
    with session.begin_nested():
        row = KnowledgeEdgeRow(
            src_id=draft.src,
            dst_id=draft.dst,
            type=draft.type.value,
            status=EdgeStatus.ACTIVE.value,
            version=1,
            provenance=draft.provenance.model_dump(mode="json"),
            created_at=now,
        )
        session.add(row)
        session.flush()
        record_event(
            session,
            "edge_added",
            "edge",
            str(row.id),
            draft.provenance.created_by,
            {"type": draft.type.value, "src": draft.src, "dst": draft.dst},
            now,
        )
        session.flush()
    return _to_edge(row)


def retract_edge(
    session: Session, edge_id: int, *, now: datetime, actor: Actor, reason: str
) -> EdgeRecord:
    """Retract a relation. The row stays for audit; it is simply no longer active."""
    row = session.get(KnowledgeEdgeRow, edge_id)
    if row is None:
        raise EdgeError(f"unknown edge {edge_id}")
    if row.status == EdgeStatus.RETRACTED.value:
        raise EdgeError(f"edge {edge_id} is already retracted")
    with session.begin_nested():
        row.status = EdgeStatus.RETRACTED.value
        row.retracted_at = now
        row.version += 1
        record_event(
            session, "edge_retracted", "edge", str(edge_id), actor, {"reason": reason}, now
        )
        session.flush()
    return _to_edge(row)


def edges_of(
    session: Session, note_id: str, *, include_retracted: bool = False
) -> list[EdgeRecord]:
    stmt = select(KnowledgeEdgeRow).where(
        or_(KnowledgeEdgeRow.src_id == note_id, KnowledgeEdgeRow.dst_id == note_id)
    )
    if not include_retracted:
        stmt = stmt.where(KnowledgeEdgeRow.status == EdgeStatus.ACTIVE.value)
    return [_to_edge(r) for r in session.scalars(stmt.order_by(KnowledgeEdgeRow.id))]


def contradictions_of(session: Session, note_id: str) -> list[str]:
    others = {
        e.dst if e.src == note_id else e.src
        for e in edges_of(session, note_id)
        if e.type is RelationType.CONTRADICTS
    }
    return sorted(others)


def independent_support_count(session: Session, note_id: str) -> int:
    """Number of independent supporters: copies of one substance and notes linked by DUPLICATES
    count once."""
    supporters = sorted(
        {
            e.src
            for e in edges_of(session, note_id)
            if e.type is RelationType.SUPPORTS and e.dst == note_id
        }
    )
    parent = {s: s for s in supporters}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    by_fingerprint: dict[str, str] = {}
    for sid in supporters:
        row = session.get(CognitiveNoteRow, sid)
        if row is None:
            continue
        first = by_fingerprint.setdefault(row.fingerprint, sid)
        union(sid, first)
    for sid in supporters:
        for e in edges_of(session, sid):
            if e.type is RelationType.DUPLICATES:
                other = e.dst if e.src == sid else e.src
                if other in parent:
                    union(sid, other)
    return len({find(s) for s in supporters})


# ------------------------------------------------------------------------------ search


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_notes(
    session: Session,
    query: str,
    *,
    types: Sequence[NoteType] | None = None,
    status: NoteStatus | None = None,
    domain: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Notes containing every term of ``query``; a title hit weighs more than a content hit."""
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []
    stmt = select(CognitiveNoteRow)
    for term in terms:
        pattern = f"%{_escape_like(term)}%"
        stmt = stmt.where(
            or_(
                CognitiveNoteRow.title.ilike(pattern, escape="\\"),
                CognitiveNoteRow.content.ilike(pattern, escape="\\"),
            )
        )
    if types:
        stmt = stmt.where(CognitiveNoteRow.type.in_([t.value for t in types]))
    if status is not None:
        stmt = stmt.where(CognitiveNoteRow.status == status.value)
    hits: list[SearchHit] = []
    for row in session.scalars(stmt.limit(500)):
        if domain is not None and row.context.get("domain") != domain:
            continue
        title, content = row.title.lower(), row.content.lower()
        score = sum(3 * (t in title) + (t in content) for t in terms)
        hits.append(SearchHit(_to_record(row), score))
    hits.sort(key=lambda h: (-h.score, -h.note.updated_at.timestamp(), h.note.note_id))
    return hits[:limit]


# ------------------------------------------------------------------------------ export


def export_markdown(session: Session, directory: Path) -> list[Path]:
    """Write one ``<id>.md`` per note into ``directory``. Read-only toward the database."""
    directory.mkdir(parents=True, exist_ok=True)
    rows = list(session.scalars(select(CognitiveNoteRow).order_by(CognitiveNoteRow.id)))
    titles = {r.id: r.title for r in rows}
    written: list[Path] = []
    for row in rows:
        if not ID_PATTERN.match(row.id):
            raise VaultError(f"refusing to export unsafe note id {row.id!r}")
        views = [
            RelationView(
                type=e.type,
                other_id=e.dst if e.src == row.id else e.src,
                direction="outgoing" if e.src == row.id else "incoming",
            )
            for e in edges_of(session, row.id)
        ]
        path = directory / f"{row.id}.md"
        path.write_text(render_note(_to_record(row), views, titles), encoding="utf-8")
        written.append(path)
    return written

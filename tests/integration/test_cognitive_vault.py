"""Cognitive Vault storage: notes, versions, relations, search, export. SQLite, no Internet."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.cognitive import orm as cog_orm
from ambre.cognitive import vault
from ambre.cognitive.models import (
    EdgeDraft,
    EvidenceLevel,
    NoteDraft,
    NoteStatus,
    NoteType,
    Provenance,
    RelationType,
)
from ambre.domain.models import Actor
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'vault.db'}")
    assert cog_orm.CognitiveNoteRow.__tablename__ in Base.metadata.tables
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def s(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session


def prov(refs: tuple[str, ...] = ("synthetic_ref",), by: Actor = Actor.AMBRE) -> Provenance:
    return Provenance(created_by=by, source_refs=refs)


def draft(title: str = "Announcement is not availability", **kw: object) -> NoteDraft:
    base: dict[str, object] = {
        "title": title,
        "type": NoteType.LESSON,
        "content": "Check the exact resolution criteria before reading an announcement.",
        "provenance": prov(),
        "context": {"domain": "technology"},
    }
    base.update(kw)
    return NoteDraft(**base)  # type: ignore[arg-type]


def count(s: Session, model: type) -> int:
    return s.scalar(select(func.count()).select_from(model)) or 0


def test_create_and_read_a_note_with_unknowns_kept_as_none(s: Session) -> None:
    rec = vault.create_note(s, draft(note_id="knowledge-tech-001"), T)
    s.commit()
    got = vault.get_note(s, "knowledge-tech-001")
    assert got == rec
    assert got is not None and got.version == 1
    assert got.confidence is None and got.valid_from is None and got.valid_until is None
    assert got.evidence_level is EvidenceLevel.NONE and got.status is NoteStatus.DRAFT
    assert got.created_at == T and got.updated_at == T
    assert got.provenance.source_refs == ("synthetic_ref",)


def test_ids_are_generated_when_absent_and_unknown_notes_return_none(s: Session) -> None:
    a = vault.create_note(s, draft("First"), T)
    b = vault.create_note(s, draft("Second", content="Different content."), T)
    assert a.note_id != b.note_id and a.note_id.startswith("note-")
    assert vault.get_note(s, "nope-nope") is None


def test_an_update_creates_a_new_version_and_keeps_every_old_one(s: Session) -> None:
    vault.create_note(s, draft(note_id="knowledge-001"), T)
    v2 = vault.update_note(
        s,
        "knowledge-001",
        expected_version=1,
        now=T + timedelta(hours=1),
        actor=Actor.AMBRE,
        reason="clarified",
        changes={"content": "Read the criteria, then the announcement."},
    )
    v3 = vault.update_note(
        s,
        "knowledge-001",
        expected_version=2,
        now=T + timedelta(hours=2),
        actor=Actor.USER,
        reason="validated by owner",
        changes={"status": NoteStatus.VALIDATED, "evidence_level": EvidenceLevel.OBSERVED},
    )
    s.commit()
    assert (v2.version, v3.version) == (2, 3)
    history = vault.note_history(s, "knowledge-001")
    assert [h.version for h in history] == [1, 2, 3]
    assert history[0].content.startswith("Check the exact")  # original text is still there
    assert history[2].status is NoteStatus.VALIDATED
    assert vault.get_note(s, "knowledge-001") == history[2]
    assert history[0].created_at == history[2].created_at == T  # creation date never moves
    assert history[2].updated_at == T + timedelta(hours=2)
    assert count(s, cog_orm.CognitiveNoteVersionRow) == 3


def test_a_stale_version_is_refused_not_overwritten(s: Session) -> None:
    vault.create_note(s, draft(note_id="knowledge-001"), T)
    vault.update_note(
        s,
        "knowledge-001",
        expected_version=1,
        now=T,
        actor=Actor.AMBRE,
        reason="a",
        changes={"title": "Edited once"},
    )
    with pytest.raises(vault.VersionConflict) as exc:
        vault.update_note(
            s,
            "knowledge-001",
            expected_version=1,
            now=T,
            actor=Actor.AMBRE,
            reason="b",
            changes={"title": "Edited from a stale read"},
        )
    assert exc.value.expected == 1 and exc.value.actual == 2
    got = vault.get_note(s, "knowledge-001")
    assert got is not None and got.title == "Edited once"


def test_two_sessions_cannot_both_win_the_same_version(factory: sessionmaker[Session]) -> None:
    with factory() as setup:
        vault.create_note(setup, draft(note_id="knowledge-001"), T)
        setup.commit()
    first, second = factory(), factory()
    try:
        vault.update_note(
            first,
            "knowledge-001",
            expected_version=1,
            now=T,
            actor=Actor.AMBRE,
            reason="x",
            changes={"title": "From first"},
        )
        first.commit()
        with pytest.raises(vault.VersionConflict):
            vault.update_note(
                second,
                "knowledge-001",
                expected_version=1,
                now=T,
                actor=Actor.AMBRE,
                reason="y",
                changes={"title": "From second"},
            )
    finally:
        first.close()
        second.close()
    with factory() as check:
        got = vault.get_note(check, "knowledge-001")
        assert got is not None and got.title == "From first" and got.version == 2


def test_a_note_type_never_changes_so_a_hypothesis_cannot_become_a_fact(s: Session) -> None:
    vault.create_note(s, draft(type=NoteType.HYPOTHESIS, note_id="hypo-001"), T)
    with pytest.raises(ValueError, match="type"):
        vault.update_note(
            s,
            "hypo-001",
            expected_version=1,
            now=T,
            actor=Actor.AMBRE,
            reason="promote",
            changes={"type": NoteType.FACT},
        )
    with pytest.raises(ValueError, match="validation"):
        vault.update_note(
            s,
            "hypo-001",
            expected_version=1,
            now=T,
            actor=Actor.AMBRE,
            reason="promote",
            changes={"status": NoteStatus.VALIDATED},
        )
    with pytest.raises(ValueError, match="unknown field"):
        vault.update_note(
            s,
            "hypo-001",
            expected_version=1,
            now=T,
            actor=Actor.AMBRE,
            reason="x",
            changes={"note_id": "other-id"},
        )
    got = vault.get_note(s, "hypo-001")
    assert got is not None and got.version == 1 and got.type is NoteType.HYPOTHESIS


def test_updating_a_missing_note_says_so(s: Session) -> None:
    with pytest.raises(vault.NoteNotFound):
        vault.update_note(
            s,
            "ghost-001",
            expected_version=1,
            now=T,
            actor=Actor.AMBRE,
            reason="x",
            changes={"title": "t"},
        )


def test_non_finite_confidence_is_refused_on_update(s: Session) -> None:
    vault.create_note(s, draft(note_id="knowledge-001"), T)
    for bad in (float("nan"), float("inf"), 2.0):
        with pytest.raises(ValueError):
            vault.update_note(
                s,
                "knowledge-001",
                expected_version=1,
                now=T,
                actor=Actor.AMBRE,
                reason="x",
                changes={"confidence": bad},
            )
    ok = vault.update_note(
        s,
        "knowledge-001",
        expected_version=1,
        now=T,
        actor=Actor.AMBRE,
        reason="measured",
        changes={"confidence": 0.8},
    )
    assert ok.confidence == 0.8


def test_exact_duplicates_are_detected_and_refused_unless_allowed(s: Session) -> None:
    first = vault.create_note(s, draft(note_id="knowledge-001"), T)
    twin = draft(
        title="  ANNOUNCEMENT is   not availability ",
        content="check the exact  resolution criteria before reading an announcement.",
    )
    assert vault.find_duplicates(s, twin) == [first.note_id]
    with pytest.raises(vault.DuplicateNote) as exc:
        vault.create_note(s, twin, T)
    assert exc.value.existing_ids == [first.note_id]
    allowed = vault.create_note(s, twin, T, allow_duplicate=True)
    assert allowed.note_id != first.note_id
    other_type = draft(type=NoteType.METHOD)
    assert vault.find_duplicates(s, other_type) == []


def test_a_storage_failure_leaves_nothing_half_written(
    s: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(vault, "_version_row", boom)
    with pytest.raises(RuntimeError):
        vault.create_note(s, draft(note_id="knowledge-001"), T)
    s.commit()  # a careless caller committing anyway must not persist half a note
    assert vault.get_note(s, "knowledge-001") is None
    assert count(s, cog_orm.CognitiveNoteRow) == 0
    assert count(s, cog_orm.CognitiveEventRow) == 0


def test_relations_are_stored_with_provenance_and_queried_both_ways(s: Session) -> None:
    vault.create_note(s, draft("Lesson", note_id="lesson-001"), T)
    vault.create_note(
        s, draft("Resolution criteria", content="Criteria text.", note_id="criteria-001"), T
    )
    edge = vault.add_edge(
        s,
        EdgeDraft(
            src="lesson-001", dst="criteria-001", type=RelationType.APPLIES_TO, provenance=prov()
        ),
        T,
    )
    s.commit()
    assert edge.src == "lesson-001" and edge.dst == "criteria-001"
    assert edge.provenance.source_refs == ("synthetic_ref",) and edge.created_at == T
    out = vault.edges_of(s, "lesson-001")
    inc = vault.edges_of(s, "criteria-001")
    assert [e.id for e in out] == [e.id for e in inc] == [edge.id]


def test_relations_to_unknown_notes_and_duplicate_relations_are_refused(s: Session) -> None:
    vault.create_note(s, draft("A", note_id="aaa-001"), T)
    vault.create_note(s, draft("B", content="b", note_id="bbb-001"), T)
    good = EdgeDraft(src="aaa-001", dst="bbb-001", type=RelationType.RELATED_TO, provenance=prov())
    with pytest.raises(vault.EdgeError, match="unknown note"):
        vault.add_edge(
            s,
            EdgeDraft(
                src="aaa-001", dst="ghost-001", type=RelationType.RELATED_TO, provenance=prov()
            ),
            T,
        )
    vault.add_edge(s, good, T)
    with pytest.raises(vault.EdgeError, match="already"):
        vault.add_edge(s, good, T)
    assert len(vault.edges_of(s, "aaa-001")) == 1


def test_a_retracted_relation_is_kept_for_audit_but_hidden_by_default(s: Session) -> None:
    vault.create_note(s, draft("A", note_id="aaa-001"), T)
    vault.create_note(s, draft("B", content="b", note_id="bbb-001"), T)
    edge = vault.add_edge(
        s,
        EdgeDraft(src="aaa-001", dst="bbb-001", type=RelationType.RELATED_TO, provenance=prov()),
        T,
    )
    vault.retract_edge(
        s, edge.id, now=T + timedelta(hours=1), actor=Actor.USER, reason="wrong link"
    )
    s.commit()
    assert vault.edges_of(s, "aaa-001") == []
    kept = vault.edges_of(s, "aaa-001", include_retracted=True)
    assert len(kept) == 1 and kept[0].status.value == "retracted"
    assert count(s, cog_orm.KnowledgeEdgeRow) == 1  # never deleted


def test_contradictions_are_found_from_either_side_and_ignore_other_relations(s: Session) -> None:
    for nid, title in (("claim-a1", "Claim A"), ("claim-b1", "Claim B"), ("claim-c1", "Claim C")):
        vault.create_note(s, draft(title, content=title, note_id=nid, type=NoteType.CLAIM), T)
    vault.add_edge(
        s,
        EdgeDraft(src="claim-a1", dst="claim-b1", type=RelationType.CONTRADICTS, provenance=prov()),
        T,
    )
    vault.add_edge(
        s,
        EdgeDraft(src="claim-c1", dst="claim-a1", type=RelationType.SUPPORTS, provenance=prov()),
        T,
    )
    assert vault.contradictions_of(s, "claim-a1") == ["claim-b1"]
    assert vault.contradictions_of(s, "claim-b1") == ["claim-a1"]
    assert vault.contradictions_of(s, "claim-c1") == []


def test_copies_of_one_source_are_not_independent_support(s: Session) -> None:
    vault.create_note(
        s, draft("Target claim", content="t", note_id="target-1", type=NoteType.CLAIM), T
    )
    # three supporters: two have identical substance (a copy), one is genuinely different
    vault.create_note(
        s, draft("Report", content="same text", note_id="sup-a1", type=NoteType.OBSERVATION), T
    )
    vault.create_note(
        s,
        draft("report", content="Same  text", note_id="sup-b1", type=NoteType.OBSERVATION),
        T,
        allow_duplicate=True,
    )
    vault.create_note(
        s,
        draft("Other report", content="different", note_id="sup-c1", type=NoteType.OBSERVATION),
        T,
    )
    for sup in ("sup-a1", "sup-b1", "sup-c1"):
        vault.add_edge(
            s, EdgeDraft(src=sup, dst="target-1", type=RelationType.SUPPORTS, provenance=prov()), T
        )
    assert vault.independent_support_count(s, "target-1") == 2  # not 3
    # and a DUPLICATES relation also merges supporters that differ in wording
    vault.add_edge(
        s, EdgeDraft(src="sup-c1", dst="sup-a1", type=RelationType.DUPLICATES, provenance=prov()), T
    )
    assert vault.independent_support_count(s, "target-1") == 1


def test_search_ranks_title_hits_first_and_filters(s: Session) -> None:
    vault.create_note(
        s,
        draft(
            "Earthquake resolution rules",
            content="USGS magnitude threshold.",
            note_id="eq-rules-1",
            context={"domain": "seismic"},
        ),
        T,
    )
    vault.create_note(
        s,
        draft(
            "Weather note",
            content="An earthquake is not weather.",
            note_id="weather-1",
            context={"domain": "weather"},
        ),
        T,
    )
    vault.create_note(s, draft("Unrelated", content="Nothing here.", note_id="other-001"), T)
    hits = vault.search_notes(s, "earthquake")
    assert [h.note.note_id for h in hits] == ["eq-rules-1", "weather-1"]
    assert hits[0].score > hits[1].score > 0
    assert [h.note.note_id for h in vault.search_notes(s, "earthquake", domain="weather")] == [
        "weather-1"
    ]
    assert vault.search_notes(s, "earthquake", types=[NoteType.METHOD]) == []
    assert vault.search_notes(s, "earthquake magnitude")[0].note.note_id == "eq-rules-1"
    assert vault.search_notes(s, "earthquake zebra") == []  # every term must match
    assert vault.search_notes(s, "   ") == []


def test_search_treats_wildcards_as_text_not_patterns(s: Session) -> None:
    vault.create_note(s, draft("100% sure", content="percent sign", note_id="pct-0001"), T)
    vault.create_note(s, draft("Other", content="plain", note_id="oth-0001"), T)
    assert [h.note.note_id for h in vault.search_notes(s, "%")] == ["pct-0001"]
    assert vault.search_notes(s, "_") == []


def test_audit_events_record_who_did_what(s: Session) -> None:
    vault.create_note(s, draft(note_id="knowledge-001"), T)
    vault.update_note(
        s,
        "knowledge-001",
        expected_version=1,
        now=T + timedelta(minutes=5),
        actor=Actor.USER,
        reason="typo",
        changes={"title": "Fixed title"},
    )
    events = vault.events_for(s, "note", "knowledge-001")
    assert [(e.kind, e.actor) for e in events] == [
        ("note_created", "AMBRE"),
        ("note_updated", "USER"),
    ]
    assert events[1].detail["reason"] == "typo"


def test_markdown_export_writes_one_safe_file_per_note_with_links(
    s: Session, tmp_path: Path
) -> None:
    vault.create_note(s, draft("Lesson", note_id="lesson-001"), T)
    vault.create_note(s, draft("Criteria", content="c", note_id="criteria-001"), T)
    vault.add_edge(
        s,
        EdgeDraft(
            src="lesson-001", dst="criteria-001", type=RelationType.APPLIES_TO, provenance=prov()
        ),
        T,
    )
    s.commit()
    out = tmp_path / "export"
    written = vault.export_markdown(s, out)
    assert sorted(p.name for p in written) == ["criteria-001.md", "lesson-001.md"]
    text = (out / "lesson-001.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---\n", 2)[1])
    assert meta["id"] == "lesson-001" and meta["confidence"] is None
    assert "- APPLIES_TO [[Criteria]]" in text
    assert "(incoming)" in (out / "criteria-001.md").read_text(encoding="utf-8")
    assert all(p.parent == out for p in written)  # nothing escapes the target directory


def test_export_never_modifies_the_database(s: Session, tmp_path: Path) -> None:
    vault.create_note(s, draft(note_id="knowledge-001"), T)
    s.commit()
    before = (count(s, cog_orm.CognitiveNoteRow), count(s, cog_orm.CognitiveEventRow))
    vault.export_markdown(s, tmp_path / "out")
    assert (count(s, cog_orm.CognitiveNoteRow), count(s, cog_orm.CognitiveEventRow)) == before

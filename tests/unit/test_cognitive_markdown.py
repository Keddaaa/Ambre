"""Markdown export of a note: controlled YAML frontmatter, readable body, wiki links (pure)."""

from datetime import UTC, datetime

import yaml

from ambre.cognitive.markdown import RelationView, render_note
from ambre.cognitive.models import (
    EvidenceLevel,
    NoteRecord,
    NoteStatus,
    NoteType,
    Provenance,
    RelationType,
)
from ambre.domain.models import Actor

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def record(**kw: object) -> NoteRecord:
    base: dict[str, object] = {
        "note_id": "knowledge-tech-001",
        "version": 3,
        "title": "Announcement is not availability",
        "type": NoteType.LESSON,
        "content": "Always read the resolution criteria.\n\n---\n\nA line of dashes is fine.",
        "status": NoteStatus.VALIDATED,
        "evidence_level": EvidenceLevel.OBSERVED,
        "context": {"domain": "technology"},
        "confidence": None,
        "provenance": Provenance(created_by=Actor.AMBRE, source_refs=("decision:abc",)),
        "created_at": T,
        "updated_at": T,
        "valid_from": None,
        "valid_until": None,
    }
    base.update(kw)
    return NoteRecord(**base)  # type: ignore[arg-type]


def split(markdown: str) -> tuple[dict[str, object], str]:
    assert markdown.startswith("---\n")
    _, front, body = markdown.split("---\n", 2)
    return yaml.safe_load(front), body


def test_frontmatter_round_trips_through_yaml_and_keeps_null_confidence() -> None:
    meta, body = split(render_note(record(), [], {}))
    assert meta["id"] == "knowledge-tech-001"
    assert meta["type"] == "LESSON" and meta["status"] == "validated"
    assert meta["version"] == 3 and meta["domain"] == "technology"
    assert meta["created_by"] == "AMBRE"
    assert "confidence" in meta and meta["confidence"] is None  # unknown stays null, not 0
    assert meta["evidence_level"] == "observed"
    assert meta["source_refs"] == ["decision:abc"]
    assert body.lstrip().startswith("# Announcement is not availability")


def test_content_is_kept_verbatim_even_with_a_line_of_dashes() -> None:
    _, body = split(render_note(record(), [], {}))
    assert "Always read the resolution criteria.\n\n---\n\nA line of dashes is fine." in body


def test_relations_become_wiki_links_with_their_type() -> None:
    rel = [
        RelationView(RelationType.SUPPORTS, "note-bbb", "outgoing"),
        RelationView(RelationType.CONTRADICTS, "note-ccc", "incoming"),
    ]
    md = render_note(record(), rel, {"note-bbb": "Resolution Criteria", "note-ccc": "Old claim"})
    assert "## Relations" in md
    assert "- SUPPORTS [[Resolution Criteria]]" in md
    assert "- CONTRADICTS (incoming) [[Old claim]]" in md


def test_a_missing_title_falls_back_to_the_id_and_brackets_cannot_break_links() -> None:
    rel = [RelationView(RelationType.RELATED_TO, "note-bbb", "outgoing")]
    assert "[[note-bbb]]" in render_note(record(), rel, {})
    nasty = render_note(record(), rel, {"note-bbb": "A [[nested]] title"})
    assert "[[A (nested) title]]" in nasty


def test_no_relations_section_when_there_are_none() -> None:
    assert "## Relations" not in render_note(record(), [], {})


def test_a_title_with_yaml_special_characters_is_still_valid_frontmatter() -> None:
    meta, body = split(render_note(record(title="Why: a 'quoted' # title"), [], {}))
    assert meta["id"] == "knowledge-tech-001"
    assert "# Why: a 'quoted' # title" in body

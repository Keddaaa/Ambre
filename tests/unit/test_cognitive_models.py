"""Cognitive Vault domain models: notes, edges, rule definitions (pure, no I/O)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ambre.cognitive.models import (
    ActionType,
    EdgeDraft,
    EvidenceLevel,
    NoteDraft,
    NoteStatus,
    NoteType,
    Provenance,
    RelationType,
    RuleAction,
    RuleDefinition,
    content_fingerprint,
)
from ambre.domain.models import Actor

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def prov(**kw: object) -> Provenance:
    base: dict[str, object] = {"created_by": Actor.AMBRE, "source_refs": ("synthetic_ref_1",)}
    base.update(kw)
    return Provenance(**base)  # type: ignore[arg-type]


def draft(**kw: object) -> NoteDraft:
    base: dict[str, object] = {
        "title": "Announcement is not availability",
        "type": NoteType.LESSON,
        "content": "Check the exact resolution criteria before reading an announcement.",
        "provenance": prov(),
    }
    base.update(kw)
    return NoteDraft(**base)  # type: ignore[arg-type]


def test_a_note_has_all_13_types() -> None:
    assert {t.value for t in NoteType} == {
        "OBSERVATION",
        "FACT",
        "CLAIM",
        "HYPOTHESIS",
        "EXPERIENCE",
        "ERROR",
        "LESSON",
        "RULE",
        "SHORTCUT",
        "METHOD",
        "STRATEGY",
        "SOURCE_PROFILE",
        "MARKET_CONTEXT",
    }


def test_defaults_never_invent_confidence_or_evidence() -> None:
    d = draft()
    assert d.confidence is None
    assert d.evidence_level is EvidenceLevel.NONE
    assert d.status is NoteStatus.DRAFT
    assert d.valid_from is None and d.valid_until is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1])
def test_confidence_must_be_none_or_a_finite_probability(bad: float) -> None:
    with pytest.raises(ValidationError):
        draft(confidence=bad)


def test_a_hypothesis_or_claim_cannot_be_born_validated() -> None:
    for kind in (NoteType.HYPOTHESIS, NoteType.CLAIM):
        with pytest.raises(ValidationError, match="validation"):
            draft(type=kind, status=NoteStatus.VALIDATED)


def test_a_fact_needs_a_source() -> None:
    with pytest.raises(ValidationError, match="source"):
        draft(type=NoteType.FACT, provenance=prov(source_refs=()))
    assert draft(type=NoteType.FACT).type is NoteType.FACT


def test_validity_window_must_be_ordered_and_aware() -> None:
    with pytest.raises(ValidationError):
        draft(valid_from=T, valid_until=T - timedelta(days=1))
    with pytest.raises(ValidationError):
        draft(valid_from=datetime(2026, 1, 1))  # naive


def test_context_only_holds_finite_scalars() -> None:
    assert (
        draft(context={"domain": "technology", "n": 3, "ok": True}).context["domain"]
        == "technology"
    )
    with pytest.raises(ValidationError):
        draft(context={"x": float("nan")})
    with pytest.raises(ValidationError):
        draft(context={"x": [1, 2]})  # type: ignore[dict-item]


@pytest.mark.parametrize("bad_id", ["", "ab", "Has Space", "UPPER", "../escape", "a/b", "x" * 80])
def test_note_id_is_a_safe_stable_slug(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        draft(note_id=bad_id)
    assert draft(note_id="knowledge-tech-001").note_id == "knowledge-tech-001"


def test_empty_title_or_oversized_content_is_refused() -> None:
    with pytest.raises(ValidationError):
        draft(title="   ")
    with pytest.raises(ValidationError):
        draft(content="x" * 20001)


def test_fingerprint_ignores_case_and_spacing_but_not_substance() -> None:
    a = content_fingerprint(NoteType.LESSON, "A  Title", "Same   content\nhere")
    b = content_fingerprint(NoteType.LESSON, "a title", "same content here")
    assert a == b
    assert a != content_fingerprint(NoteType.LESSON, "a title", "other content")
    assert a != content_fingerprint(NoteType.FACT, "a title", "same content here")


def test_an_edge_cannot_link_a_note_to_itself() -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(src="note-aaa", dst="note-aaa", type=RelationType.RELATED_TO, provenance=prov())


def test_factual_edges_need_a_source_not_just_similarity() -> None:
    for kind in (RelationType.SUPPORTS, RelationType.CONTRADICTS, RelationType.CAUSED_ERROR):
        with pytest.raises(ValidationError, match="source"):
            EdgeDraft(src="note-aaa", dst="note-bbb", type=kind, provenance=prov(source_refs=()))
        with pytest.raises(ValidationError, match="similarity"):
            EdgeDraft(
                src="note-aaa",
                dst="note-bbb",
                type=kind,
                provenance=prov(method="embedding_similarity"),
            )
    ok = EdgeDraft(src="note-aaa", dst="note-bbb", type=RelationType.SUPPORTS, provenance=prov())
    assert ok.type is RelationType.SUPPORTS


def test_a_non_factual_edge_may_be_justified_by_a_note() -> None:
    e = EdgeDraft(
        src="note-aaa",
        dst="note-bbb",
        type=RelationType.RELATED_TO,
        provenance=prov(source_refs=(), note="same market family"),
    )
    assert e.provenance.note == "same market family"
    with pytest.raises(ValidationError):
        EdgeDraft(
            src="note-aaa",
            dst="note-bbb",
            type=RelationType.RELATED_TO,
            provenance=prov(source_refs=()),
        )


def test_rule_definition_requires_the_hard_limit_flag_to_exist_and_nan_is_refused() -> None:
    d = RuleDefinition(
        name="PreferHistoricalForecasts",
        domain="weather",
        conditions={"market_type": "binary"},
        action=RuleAction(type=ActionType.PRIORITIZE_SOURCE, params={"source_category": "archive"}),
    )
    assert d.restrictions.cannot_override_hard_limits is True
    with pytest.raises(ValidationError):
        RuleAction(
            type=ActionType.TIGHTEN_LIMIT, params={"limit": "kelly_fraction", "value": float("nan")}
        )
    with pytest.raises(ValidationError):
        RuleDefinition(
            name="bad name",
            domain="weather",
            action=RuleAction(type=ActionType.PRIORITIZE_SOURCE, params={}),
        )


def test_unknown_action_type_is_refused() -> None:
    with pytest.raises(ValidationError):
        RuleAction(type="PLACE_BET", params={})  # type: ignore[arg-type]

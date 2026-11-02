"""Rule registry and the first full Cognitive Vault journey. SQLite, no Internet, no bets."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.cognitive import orm as cog_orm
from ambre.cognitive import rules, vault
from ambre.cognitive.constraints import CurrentLimits
from ambre.cognitive.models import (
    ActionType,
    EdgeDraft,
    EvidenceLevel,
    NoteDraft,
    NoteType,
    Provenance,
    RelationType,
    RuleAction,
    RuleDefinition,
    RuleEvidence,
    RuleRestrictions,
    RuleStatus,
)
from ambre.config.settings import RiskConfig
from ambre.domain.models import Actor
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, PositionRow

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LIMITS = CurrentLimits.from_risk(RiskConfig())
GOOD = RuleEvidence(
    sample_count=45, validation_score=0.08, method="held_out_backtest", held_out=True
)


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'vault.db'}")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def s(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        vault.create_note(
            session,
            NoteDraft(
                title="Seismic feed answered with unreviewed events",
                type=NoteType.OBSERVATION,
                content="Three unreviewed solutions were later revised.",
                provenance=Provenance(created_by=Actor.AMBRE, source_refs=("intel_request:r1",)),
                note_id="obs-seismic-001",
            ),
            T,
        )
        session.commit()
        yield session


def definition(name: str = "PreferReviewedSolutions", **kw: object) -> RuleDefinition:
    base: dict[str, object] = {
        "name": name,
        "domain": "seismic",
        "conditions": {"market_type": "binary"},
        "action": RuleAction(
            type=ActionType.PRIORITIZE_SOURCE, params={"source_category": "reviewed_catalog"}
        ),
    }
    base.update(kw)
    return RuleDefinition(**base)  # type: ignore[arg-type]


def propose(s: Session, d: RuleDefinition | None = None, **kw: object) -> rules.ProposalOutcome:
    args: dict[str, object] = {
        "source_note_ids": ("obs-seismic-001",),
        "actor": Actor.AMBRE,
        "now": T,
    }
    args.update(kw)
    return rules.propose_rule(s, d or definition(), LIMITS, **args)  # type: ignore[arg-type]


def count(s: Session, model: type) -> int:
    return s.scalar(select(func.count()).select_from(model)) or 0


# ------------------------------------------------------------------- the first journey


def test_first_journey_observation_to_inactive_rule_proposal_all_persisted(s: Session) -> None:
    before = [count(s, m) for m in (DecisionRow, PositionRow, LedgerRow)]
    # 1. an observation is recorded (it already exists as obs-seismic-001)
    obs = vault.get_note(s, "obs-seismic-001")
    assert obs is not None and obs.type is NoteType.OBSERVATION
    # 2. it becomes a lesson note, linked to the observation it comes from
    lesson = vault.create_note(
        s,
        NoteDraft(
            title="Prefer reviewed seismic solutions",
            type=NoteType.LESSON,
            content="Automatic solutions are revised; read reviewed ones before linking a market.",
            provenance=Provenance(created_by=Actor.AMBRE, source_refs=("note:obs-seismic-001",)),
            context={"domain": "seismic"},
            note_id="lesson-seismic-001",
        ),
        T + timedelta(minutes=1),
    )
    vault.add_edge(
        s,
        EdgeDraft(
            src=lesson.note_id,
            dst=obs.note_id,
            type=RelationType.DERIVED_FROM,
            provenance=Provenance(created_by=Actor.AMBRE, source_refs=("note:obs-seismic-001",)),
        ),
        T + timedelta(minutes=1),
    )
    # 3. a rule is proposed from the lesson, 4. checked structurally, 5. left inactive
    out = propose(s, source_note_ids=(lesson.note_id, obs.note_id), now=T + timedelta(minutes=2))
    s.commit()

    assert out.accepted and out.reasons == []
    assert out.rule.status is RuleStatus.PROPOSED and out.rule.version == 2
    assert rules.active_rules(s) == []  # a proposal changes no behaviour
    assert rules.get_rule(s, out.rule.rule_id) == out.rule

    # 6. everything is persisted and consultable
    history = rules.rule_history(s, out.rule.rule_id)
    assert [(h.version, h.status) for h in history] == [
        (1, RuleStatus.DRAFT),
        (2, RuleStatus.PROPOSED),
    ]
    assert "structural validation passed" in (history[1].reason or "")
    assert out.rule.source_note_ids == (lesson.note_id, obs.note_id)
    assert [e.kind for e in vault.events_for(s, "rule", out.rule.rule_id)] == ["rule_proposed"]
    assert [e.dst for e in vault.edges_of(s, lesson.note_id)] == [obs.note_id]
    assert vault.search_notes(s, "reviewed seismic")[0].note.note_id == "lesson-seismic-001"
    assert [
        count(s, m) for m in (DecisionRow, PositionRow, LedgerRow)
    ] == before  # no bet path touched


# ------------------------------------------------------------------------ proposals


def test_a_new_rule_is_never_active(s: Session) -> None:
    out = propose(s)
    assert out.rule.status is RuleStatus.PROPOSED
    assert not rules.is_active(out.rule)
    assert rules.active_rules(s) == []


def test_an_invalid_rule_is_refused_kept_as_rejected_and_explained(s: Session) -> None:
    looser = definition(
        "LoosenKelly",
        action=RuleAction(
            type=ActionType.TIGHTEN_LIMIT, params={"limit": "kelly_fraction", "value": 0.9}
        ),
    )
    out = propose(s, looser)
    s.commit()
    assert not out.accepted and out.rule.status is RuleStatus.REJECTED
    assert any("loosen" in r for r in out.reasons)
    history = rules.rule_history(s, out.rule.rule_id)
    assert len(history) == 1 and history[0].status is RuleStatus.REJECTED
    assert "loosen" in (history[0].reason or "")
    assert rules.active_rules(s) == []
    kinds = [e.kind for e in vault.events_for(s, "rule", out.rule.rule_id)]
    assert kinds == ["rule_rejected"]


def test_waiving_the_hard_limits_is_refused(s: Session) -> None:
    d = definition(restrictions=RuleRestrictions(cannot_override_hard_limits=False))
    out = propose(s, d)
    assert not out.accepted and any("hard limits" in r for r in out.reasons)


def test_a_rule_needs_an_existing_source_note_so_its_origin_is_traceable(s: Session) -> None:
    none = propose(s, source_note_ids=())
    ghost = propose(s, definition("OtherRule"), source_note_ids=("ghost-note-1",))
    assert not none.accepted and any("source note" in r for r in none.reasons)
    assert not ghost.accepted and any("unknown source note" in r for r in ghost.reasons)


def test_rule_ids_are_stable_and_unique(s: Session) -> None:
    out = propose(s, rule_id="rule_seismic_001")
    assert out.rule.rule_id == "rule_seismic_001"
    with pytest.raises(rules.RuleExists):
        propose(s, definition("AnotherName"), rule_id="rule_seismic_001")


# ----------------------------------------------------------------------- lifecycle


def walk_to_validated(s: Session, rule_id: str) -> rules.RuleRecord:
    r = rules.transition_rule(
        s,
        rule_id,
        RuleStatus.TESTING,
        actor=Actor.AMBRE,
        reason="start held-out test",
        expected_version=2,
        now=T + timedelta(hours=1),
        limits=LIMITS,
    )
    return rules.transition_rule(
        s,
        rule_id,
        RuleStatus.VALIDATED,
        actor=Actor.USER,
        reason="held-out backtest reviewed",
        expected_version=r.version,
        now=T + timedelta(hours=2),
        limits=LIMITS,
        evidence=GOOD,
    )


def test_the_full_lifecycle_activates_only_after_measured_validation(s: Session) -> None:
    rid = propose(s).rule.rule_id
    validated = walk_to_validated(s, rid)
    assert validated.status is RuleStatus.VALIDATED and validated.evidence == GOOD
    assert rules.active_rules(s) == []  # validated is still inactive
    active = rules.transition_rule(
        s,
        rid,
        RuleStatus.ACTIVE,
        actor=Actor.USER,
        reason="owner activation",
        expected_version=validated.version,
        now=T + timedelta(hours=3),
        limits=LIMITS,
    )
    s.commit()
    assert [r.rule_id for r in rules.active_rules(s)] == [rid]
    assert active.version == 5
    assert [h.status for h in rules.rule_history(s, rid)] == [
        RuleStatus.DRAFT,
        RuleStatus.PROPOSED,
        RuleStatus.TESTING,
        RuleStatus.VALIDATED,
        RuleStatus.ACTIVE,
    ]
    # every earlier version is still readable, with its own evidence state
    history = rules.rule_history(s, rid)
    assert history[1].evidence is None and history[3].evidence == GOOD


def test_a_rule_cannot_skip_testing_or_jump_to_active(s: Session) -> None:
    rid = propose(s).rule.rule_id
    for target in (RuleStatus.VALIDATED, RuleStatus.ACTIVE, RuleStatus.DEPRECATED):
        with pytest.raises(rules.TransitionRefused) as exc:
            rules.transition_rule(
                s,
                rid,
                target,
                actor=Actor.USER,
                reason="shortcut",
                expected_version=2,
                now=T,
                limits=LIMITS,
                evidence=GOOD,
            )
        assert any("not a legal transition" in r for r in exc.value.reasons)
    current = rules.get_rule(s, rid)
    assert current is not None and current.status is RuleStatus.PROPOSED and current.version == 2


def test_validation_without_measured_held_out_evidence_is_refused(s: Session) -> None:
    rid = propose(s).rule.rule_id
    rules.transition_rule(
        s,
        rid,
        RuleStatus.TESTING,
        actor=Actor.AMBRE,
        reason="test",
        expected_version=2,
        now=T,
        limits=LIMITS,
    )
    for evidence in (
        None,
        RuleEvidence(),
        RuleEvidence(
            sample_count=3, validation_score=0.5, method="held_out_backtest", held_out=True
        ),
        RuleEvidence(
            sample_count=99, validation_score=0.5, method="held_out_backtest", held_out=False
        ),
    ):
        with pytest.raises(rules.TransitionRefused):
            rules.transition_rule(
                s,
                rid,
                RuleStatus.VALIDATED,
                actor=Actor.USER,
                reason="try",
                expected_version=3,
                now=T,
                limits=LIMITS,
                evidence=evidence,
            )
    current = rules.get_rule(s, rid)
    assert current is not None and current.status is RuleStatus.TESTING


def test_ambre_cannot_promote_its_own_rule_because_its_evidence_is_self_reported(
    s: Session,
) -> None:
    rid = propose(s).rule.rule_id
    rules.transition_rule(
        s,
        rid,
        RuleStatus.TESTING,
        actor=Actor.AMBRE,
        reason="test",
        expected_version=2,
        now=T,
        limits=LIMITS,
    )
    for actor in (Actor.AMBRE, Actor.CLAUDE):
        with pytest.raises(rules.TransitionRefused) as exc:
            rules.transition_rule(
                s,
                rid,
                RuleStatus.VALIDATED,
                actor=actor,
                reason="my own evidence",
                expected_version=3,
                now=T,
                limits=LIMITS,
                evidence=GOOD,
            )
        assert any("owner" in r for r in exc.value.reasons)


def test_a_refused_transition_changes_nothing_and_is_audited(s: Session) -> None:
    rid = propose(s).rule.rule_id
    with pytest.raises(rules.TransitionRefused):
        rules.transition_rule(
            s,
            rid,
            RuleStatus.ACTIVE,
            actor=Actor.AMBRE,
            reason="please",
            expected_version=2,
            now=T,
            limits=LIMITS,
            evidence=GOOD,
        )
    s.commit()
    assert len(rules.rule_history(s, rid)) == 2
    kinds = [e.kind for e in vault.events_for(s, "rule", rid)]
    assert kinds == ["rule_proposed", "rule_transition_refused"]


def test_rejection_is_possible_before_activation_and_is_final(s: Session) -> None:
    rid = propose(s).rule.rule_id
    rules.transition_rule(
        s,
        rid,
        RuleStatus.REJECTED,
        actor=Actor.USER,
        reason="not useful",
        expected_version=2,
        now=T,
        limits=LIMITS,
    )
    with pytest.raises(rules.TransitionRefused):
        rules.transition_rule(
            s,
            rid,
            RuleStatus.TESTING,
            actor=Actor.USER,
            reason="retry",
            expected_version=3,
            now=T,
            limits=LIMITS,
        )
    assert rules.active_rules(s) == []


def test_an_active_rule_can_be_rolled_back_and_stops_being_active_but_stays_in_history(
    s: Session,
) -> None:
    rid = propose(s).rule.rule_id
    v = walk_to_validated(s, rid)
    a = rules.transition_rule(
        s,
        rid,
        RuleStatus.ACTIVE,
        actor=Actor.USER,
        reason="go",
        expected_version=v.version,
        now=T,
        limits=LIMITS,
    )
    back = rules.transition_rule(
        s,
        rid,
        RuleStatus.ROLLED_BACK,
        actor=Actor.USER,
        reason="regression seen",
        expected_version=a.version,
        now=T + timedelta(days=1),
        limits=LIMITS,
    )
    assert back.status is RuleStatus.ROLLED_BACK and rules.active_rules(s) == []
    statuses = [h.status for h in rules.rule_history(s, rid)]
    assert RuleStatus.ACTIVE in statuses and statuses[-1] is RuleStatus.ROLLED_BACK
    dep = rules.transition_rule(
        s,
        rid,
        RuleStatus.DEPRECATED,
        actor=Actor.USER,
        reason="retired",
        expected_version=back.version,
        now=T,
        limits=LIMITS,
    )
    assert dep.status is RuleStatus.DEPRECATED


def test_a_stale_rule_version_is_refused(s: Session) -> None:
    rid = propose(s).rule.rule_id
    with pytest.raises(rules.RuleVersionConflict) as exc:
        rules.transition_rule(
            s,
            rid,
            RuleStatus.TESTING,
            actor=Actor.AMBRE,
            reason="x",
            expected_version=1,
            now=T,
            limits=LIMITS,
        )
    assert exc.value.expected == 1 and exc.value.actual == 2


def test_two_sessions_cannot_both_move_the_same_rule_version(
    factory: sessionmaker[Session],
) -> None:
    with factory() as setup:
        vault.create_note(
            setup,
            NoteDraft(
                title="Source",
                type=NoteType.OBSERVATION,
                content="o",
                provenance=Provenance(created_by=Actor.AMBRE, source_refs=("x",)),
                note_id="obs-seismic-001",
            ),
            T,
        )
        rid = propose(setup).rule.rule_id
        setup.commit()
    a, b = factory(), factory()
    try:
        rules.transition_rule(
            a,
            rid,
            RuleStatus.TESTING,
            actor=Actor.AMBRE,
            reason="a",
            expected_version=2,
            now=T,
            limits=LIMITS,
        )
        a.commit()
        with pytest.raises(rules.RuleVersionConflict):
            rules.transition_rule(
                b,
                rid,
                RuleStatus.REJECTED,
                actor=Actor.USER,
                reason="b",
                expected_version=2,
                now=T,
                limits=LIMITS,
            )
    finally:
        a.close()
        b.close()


# ------------------------------------------------------------------ constraints at runtime


def test_activation_rechecks_the_rule_against_the_limits_in_force_today(s: Session) -> None:
    ok_today = definition(
        "TightenKellyALittle",
        action=RuleAction(
            type=ActionType.TIGHTEN_LIMIT, params={"limit": "kelly_fraction", "value": 0.2}
        ),
    )
    rid = propose(s, ok_today).rule.rule_id
    v = walk_to_validated(s, rid)
    stricter_now = CurrentLimits.from_risk(RiskConfig(kelly_fraction=0.1))
    with pytest.raises(rules.TransitionRefused) as exc:
        rules.transition_rule(
            s,
            rid,
            RuleStatus.ACTIVE,
            actor=Actor.USER,
            reason="go",
            expected_version=v.version,
            now=T,
            limits=stricter_now,
        )
    assert any("loosen" in r for r in exc.value.reasons)  # 0.2 would now loosen the 0.1 limit


def test_conflicting_rules_are_flagged_and_cannot_be_active_together(s: Session) -> None:
    first = propose(
        s,
        definition(
            "PreferArchive",
            action=RuleAction(
                type=ActionType.PRIORITIZE_SOURCE, params={"source_category": "archive"}
            ),
        ),
    )
    second = propose(
        s,
        definition(
            "AvoidArchive",
            action=RuleAction(
                type=ActionType.DEPRIORITIZE_SOURCE, params={"source_category": "archive"}
            ),
        ),
    )
    assert first.conflicts == []
    assert second.accepted and [c.rule_id for c in second.conflicts] == [first.rule.rule_id]
    assert "archive" in second.conflicts[0].reason
    v1 = walk_to_validated(s, first.rule.rule_id)
    rules.transition_rule(
        s,
        first.rule.rule_id,
        RuleStatus.ACTIVE,
        actor=Actor.USER,
        reason="go",
        expected_version=v1.version,
        now=T,
        limits=LIMITS,
    )
    v2 = walk_to_validated(s, second.rule.rule_id)
    with pytest.raises(rules.TransitionRefused) as exc:
        rules.transition_rule(
            s,
            second.rule.rule_id,
            RuleStatus.ACTIVE,
            actor=Actor.USER,
            reason="go",
            expected_version=v2.version,
            now=T,
            limits=LIMITS,
        )
    assert any("conflicts with active rule" in r for r in exc.value.reasons)
    assert [r.rule_id for r in rules.active_rules(s)] == [first.rule.rule_id]


def test_the_risk_configuration_is_untouched_by_the_whole_rule_machinery(s: Session) -> None:
    before = RiskConfig().model_dump()
    rid = propose(s).rule.rule_id
    walk_to_validated(s, rid)
    assert RiskConfig().model_dump() == before
    assert CurrentLimits.from_risk(RiskConfig()) == LIMITS


def test_notes_and_rules_can_live_side_by_side_with_evidence_levels(s: Session) -> None:
    n = vault.get_note(s, "obs-seismic-001")
    assert n is not None and n.evidence_level is EvidenceLevel.NONE
    assert count(s, cog_orm.RuleProposalRow) == 0
    propose(s)
    assert count(s, cog_orm.RuleProposalRow) == 1 and count(s, cog_orm.RuleVersionRow) == 2

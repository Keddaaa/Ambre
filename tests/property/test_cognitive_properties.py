"""Property tests: the immutable constraints hold under every proposal sequence AMBRE could make."""

import math
import string
from datetime import UTC, datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import func, select

from ambre.cognitive import orm as cog_orm
from ambre.cognitive import rules, vault
from ambre.cognitive.constraints import (
    LOWER_ONLY,
    PROTECTED_TOKENS,
    RAISE_ONLY,
    CurrentLimits,
    validate_definition,
)
from ambre.cognitive.lifecycle import MIN_VALIDATION_SAMPLES, TRANSITIONS, check_transition
from ambre.cognitive.models import (
    ActionType,
    NoteDraft,
    NoteType,
    Provenance,
    RuleAction,
    RuleDefinition,
    RuleEvidence,
    RuleRestrictions,
    RuleStatus,
    content_fingerprint,
)
from ambre.config.settings import RiskConfig
from ambre.domain.models import Actor
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, PositionRow

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LIMITS = CurrentLimits.from_risk(RiskConfig())
SNAPSHOT = (frozenset(LOWER_ONLY), frozenset(RAISE_ONLY), frozenset(PROTECTED_TOKENS))
ALLOWED_KEYS = {
    ActionType.PRIORITIZE_SOURCE: {"source_category"},
    ActionType.DEPRIORITIZE_SOURCE: {"source_category"},
    ActionType.PREFER_OFFICIAL_SOURCES: set(),
    ActionType.REDUCE_API_CALLS: {"provider_id", "factor"},
    ActionType.TIGHTEN_LIMIT: {"limit", "value"},
    ActionType.RAISE_CONFIDENCE_THRESHOLD: {"threshold"},
    ActionType.SUSPEND_STRATEGY: {"strategy"},
    ActionType.ADD_OBSERVATION_PERIOD: {"seconds"},
}

KEYS = st.sampled_from(
    [
        "source_category",
        "provider_id",
        "factor",
        "limit",
        "value",
        "threshold",
        "strategy",
        "seconds",
    ]
    + sorted(PROTECTED_TOKENS)
    + ["extra", "x"]
)
TEXTS = st.one_of(
    st.sampled_from(
        sorted(LOWER_ONLY | RAISE_ONLY)
        + [
            "archive",
            "usgs_earthquakes",
            "VALUE_HUNTER",
            "execution_mode",
            "enable_manifold_orders",
        ]
    ),
    st.text(max_size=20),
)
NUMBERS = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False, min_value=-5, max_value=5),
    st.integers(min_value=-5, max_value=100000),
)
PARAMS = st.dictionaries(KEYS, st.one_of(TEXTS, NUMBERS, st.booleans()), max_size=4)


@st.composite
def definitions(draw: st.DrawFn) -> RuleDefinition:
    return RuleDefinition(
        name="GeneratedRule",
        domain=draw(st.sampled_from(["weather", "seismic", "news"])),
        action=RuleAction(type=draw(st.sampled_from(list(ActionType))), params=draw(PARAMS)),
        restrictions=RuleRestrictions(cannot_override_hard_limits=draw(st.booleans())),
    )


@settings(max_examples=400, deadline=None)
@given(definitions())
def test_an_accepted_rule_can_never_loosen_touch_or_waive_anything_protected(
    d: RuleDefinition,
) -> None:
    if validate_definition(d, LIMITS):
        return
    action = d.action
    assert d.restrictions.cannot_override_hard_limits is True
    assert set(action.params) <= ALLOWED_KEYS[action.type]
    texts = [str(k).lower() for k in action.params] + [
        str(v).lower() for v in action.params.values()
    ]
    assert not any(tok in t for tok in PROTECTED_TOKENS for t in texts)
    if action.type is ActionType.TIGHTEN_LIMIT:
        name, value = action.params["limit"], float(action.params["value"])  # type: ignore[arg-type]
        assert math.isfinite(value) and value > 0
        current = float(getattr(LIMITS, str(name)))
        assert (name in LOWER_ONLY and value <= current) or (
            name in RAISE_ONLY and value >= current
        )
    if action.type is ActionType.REDUCE_API_CALLS:
        assert 0 < float(action.params["factor"]) <= 1  # type: ignore[arg-type]
    if action.type is ActionType.RAISE_CONFIDENCE_THRESHOLD:
        assert 0 < float(action.params["threshold"]) <= 1  # type: ignore[arg-type]


EVIDENCE = st.sampled_from(
    [
        None,
        RuleEvidence(),
        RuleEvidence(
            sample_count=2, validation_score=0.3, method="held_out_backtest", held_out=True
        ),
        RuleEvidence(
            sample_count=99, validation_score=0.3, method="held_out_backtest", held_out=False
        ),
        RuleEvidence(
            sample_count=MIN_VALIDATION_SAMPLES,
            validation_score=0.3,
            method="paired_experiment",
            held_out=True,
        ),
    ]
)


@settings(max_examples=400, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(list(RuleStatus)), EVIDENCE), max_size=15))
def test_no_sequence_of_attempts_reaches_active_without_testing_validation_and_evidence(
    attempts: list[tuple[RuleStatus, RuleEvidence | None]],
) -> None:
    status = RuleStatus.PROPOSED
    path = [status]
    for target, evidence in attempts:
        if check_transition(status, target, evidence) == []:
            if target in (RuleStatus.VALIDATED, RuleStatus.ACTIVE):
                assert evidence is not None and evidence.held_out is True
                assert evidence.sample_count is not None
                assert evidence.sample_count >= MIN_VALIDATION_SAMPLES
            status = target
            path.append(status)
    for before, after in zip(path, path[1:], strict=False):
        assert after in TRANSITIONS[before]
        if after is RuleStatus.ACTIVE:
            assert before is RuleStatus.VALIDATED
        if after is RuleStatus.VALIDATED:
            assert before is RuleStatus.TESTING
        assert before not in (RuleStatus.REJECTED, RuleStatus.DEPRECATED)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(st.lists(definitions(), min_size=1, max_size=8))
def test_any_stream_of_proposals_leaves_the_limits_and_the_bet_path_untouched(
    stream: list[RuleDefinition],
) -> None:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with make_session_factory(engine)() as s:
            vault.create_note(
                s,
                NoteDraft(
                    title="Source",
                    type=NoteType.OBSERVATION,
                    content="o",
                    provenance=Provenance(created_by=Actor.AMBRE, source_refs=("x",)),
                    note_id="obs-source-001",
                ),
                T,
            )
            risk_before = RiskConfig().model_dump()
            for i, d in enumerate(stream):
                out = rules.propose_rule(
                    s,
                    d,
                    LIMITS,
                    source_note_ids=("obs-source-001",),
                    actor=Actor.AMBRE,
                    now=T,
                    rule_id=f"rule-{i:03d}",
                )
                assert out.rule.status in (RuleStatus.PROPOSED, RuleStatus.REJECTED)
            s.commit()
            assert rules.active_rules(s) == []
            assert RiskConfig().model_dump() == risk_before
            assert (
                frozenset(LOWER_ONLY),
                frozenset(RAISE_ONLY),
                frozenset(PROTECTED_TOKENS),
            ) == SNAPSHOT
            for model in (DecisionRow, PositionRow, LedgerRow):
                assert (s.scalar(select(func.count()).select_from(model)) or 0) == 0
            versions = s.scalar(select(func.count()).select_from(cog_orm.RuleVersionRow)) or 0
            assert versions >= len(stream)  # every proposal left a trace, accepted or not
    finally:
        engine.dispose()


@given(st.floats() | st.none())
def test_confidence_is_accepted_only_when_none_or_a_finite_probability(value: float | None) -> None:
    base = {
        "title": "t",
        "type": NoteType.LESSON,
        "content": "c",
        "provenance": Provenance(created_by=Actor.AMBRE),
    }
    valid = value is None or (math.isfinite(value) and 0.0 <= value <= 1.0)
    try:
        NoteDraft(confidence=value, **base)  # type: ignore[arg-type]
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted == valid


ASCII = st.text(alphabet=string.ascii_letters + string.digits + " ", max_size=60)


@given(ASCII, ASCII, st.integers(0, 3), st.integers(0, 3))
def test_fingerprint_ignores_case_and_extra_whitespace(
    title: str, body: str, a: int, b: int
) -> None:
    padded_title = " " * a + title.upper() + " " * b
    padded_body = body.replace(" ", " " * (a + 1)).upper()
    assert content_fingerprint(NoteType.LESSON, title, body) == content_fingerprint(
        NoteType.LESSON, padded_title, padded_body
    )

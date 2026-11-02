"""Level A/B/C constraints, rule validation, conflicts and the lifecycle (pure)."""

import dataclasses

import pytest

from ambre.cognitive.constraints import (
    LOWER_ONLY,
    PROTECTED_TOKENS,
    RAISE_ONLY,
    CurrentLimits,
    level_of,
    rules_conflict,
    validate_definition,
)
from ambre.cognitive.lifecycle import TRANSITIONS, check_transition, is_operative
from ambre.cognitive.models import (
    ActionType,
    RuleAction,
    RuleDefinition,
    RuleEvidence,
    RuleRestrictions,
    RuleStatus,
)
from ambre.config.settings import RiskConfig

LIMITS = CurrentLimits.from_risk(RiskConfig())


def rule(action_type: ActionType, params: dict[str, object], **kw: object) -> RuleDefinition:
    base: dict[str, object] = {
        "name": "SyntheticRule",
        "domain": "weather",
        "action": RuleAction(type=action_type, params=params),  # type: ignore[arg-type]
    }
    base.update(kw)
    return RuleDefinition(**base)  # type: ignore[arg-type]


def test_limits_mirror_the_current_risk_config() -> None:
    assert LIMITS.max_position_fraction == 0.02
    assert LIMITS.max_total_exposure_fraction == 0.20
    assert LIMITS.kelly_fraction == 0.25
    assert LIMITS.minimum_edge == 0.03
    assert LIMITS.min_stake == 1.0


def test_the_limits_object_cannot_be_modified() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        LIMITS.max_position_fraction = 1.0  # type: ignore[misc]


def test_a_valid_preference_rule_has_no_reasons() -> None:
    d = rule(ActionType.PRIORITIZE_SOURCE, {"source_category": "forecast_archive"})
    assert validate_definition(d, LIMITS) == []


def test_levels_separate_restrictions_from_preferences() -> None:
    assert level_of(ActionType.TIGHTEN_LIMIT) == "B"
    assert level_of(ActionType.SUSPEND_STRATEGY) == "B"
    assert level_of(ActionType.PRIORITIZE_SOURCE) == "C"
    assert level_of(ActionType.PREFER_OFFICIAL_SOURCES) == "C"


@pytest.mark.parametrize("limit", sorted(LOWER_ONLY))
def test_a_limit_may_be_lowered_or_kept_never_raised(limit: str) -> None:
    current = getattr(LIMITS, limit)
    ok = rule(ActionType.TIGHTEN_LIMIT, {"limit": limit, "value": current / 2})
    same = rule(ActionType.TIGHTEN_LIMIT, {"limit": limit, "value": current})
    looser = rule(ActionType.TIGHTEN_LIMIT, {"limit": limit, "value": current * 1.01})
    assert validate_definition(ok, LIMITS) == []
    assert validate_definition(same, LIMITS) == []
    assert any("loosen" in r for r in validate_definition(looser, LIMITS))


@pytest.mark.parametrize("limit", sorted(RAISE_ONLY))
def test_a_floor_may_be_raised_or_kept_never_lowered(limit: str) -> None:
    current = getattr(LIMITS, limit)
    assert (
        validate_definition(
            rule(ActionType.TIGHTEN_LIMIT, {"limit": limit, "value": current * 2}), LIMITS
        )
        == []
    )
    lower = rule(ActionType.TIGHTEN_LIMIT, {"limit": limit, "value": current * 0.5})
    assert any("loosen" in r for r in validate_definition(lower, LIMITS))


def test_unknown_or_protected_limit_names_are_refused() -> None:
    for name in ("execution_mode", "critical_drawdown", "failure_value_fraction", "unknown"):
        reasons = validate_definition(
            rule(ActionType.TIGHTEN_LIMIT, {"limit": name, "value": 0.01}), LIMITS
        )
        assert reasons, name


def test_negative_zero_or_missing_values_are_refused() -> None:
    for params in (
        {"limit": "kelly_fraction", "value": -0.1},
        {"limit": "kelly_fraction", "value": 0},
        {"limit": "kelly_fraction"},
        {"value": 0.1},
    ):
        assert validate_definition(rule(ActionType.TIGHTEN_LIMIT, params), LIMITS), params


def test_unknown_parameters_are_refused_so_a_typo_cannot_hide_something() -> None:
    d = rule(ActionType.PRIORITIZE_SOURCE, {"source_category": "a", "extra": "x"})
    assert any("unexpected parameter" in r for r in validate_definition(d, LIMITS))


@pytest.mark.parametrize("token", sorted(PROTECTED_TOKENS))
def test_protected_names_cannot_appear_anywhere_in_the_action(token: str) -> None:
    by_value = rule(ActionType.PRIORITIZE_SOURCE, {"source_category": f"x_{token}_y"})
    assert any("protected" in r for r in validate_definition(by_value, LIMITS))
    by_key = rule(ActionType.PRIORITIZE_SOURCE, {"source_category": "a", token.upper(): "x"})
    assert any("protected" in r for r in validate_definition(by_key, LIMITS))


def test_a_rule_that_waives_the_hard_limits_is_refused() -> None:
    d = rule(
        ActionType.PRIORITIZE_SOURCE,
        {"source_category": "a"},
        restrictions=RuleRestrictions(cannot_override_hard_limits=False),
    )
    assert any("hard limits" in r for r in validate_definition(d, LIMITS))


def test_each_action_validates_its_own_parameters() -> None:
    assert (
        validate_definition(
            rule(ActionType.REDUCE_API_CALLS, {"provider_id": "usgs_earthquakes", "factor": 0.5}),
            LIMITS,
        )
        == []
    )
    assert validate_definition(
        rule(ActionType.REDUCE_API_CALLS, {"provider_id": "usgs_earthquakes", "factor": 1.5}),
        LIMITS,
    )  # a factor above 1 would increase calls
    assert (
        validate_definition(rule(ActionType.RAISE_CONFIDENCE_THRESHOLD, {"threshold": 0.7}), LIMITS)
        == []
    )
    assert validate_definition(
        rule(ActionType.RAISE_CONFIDENCE_THRESHOLD, {"threshold": 1.5}), LIMITS
    )
    assert (
        validate_definition(rule(ActionType.ADD_OBSERVATION_PERIOD, {"seconds": 3600}), LIMITS)
        == []
    )
    assert validate_definition(rule(ActionType.ADD_OBSERVATION_PERIOD, {"seconds": 0}), LIMITS)
    assert (
        validate_definition(rule(ActionType.SUSPEND_STRATEGY, {"strategy": "VALUE_HUNTER"}), LIMITS)
        == []
    )
    assert validate_definition(rule(ActionType.PREFER_OFFICIAL_SOURCES, {}), LIMITS) == []
    assert validate_definition(rule(ActionType.PRIORITIZE_SOURCE, {}), LIMITS)


def test_contradictory_source_preferences_conflict() -> None:
    a = rule(ActionType.PRIORITIZE_SOURCE, {"source_category": "archive"})
    b = rule(ActionType.DEPRIORITIZE_SOURCE, {"source_category": "archive"})
    c = rule(ActionType.DEPRIORITIZE_SOURCE, {"source_category": "other"})
    other_domain = rule(
        ActionType.DEPRIORITIZE_SOURCE, {"source_category": "archive"}, domain="news"
    )
    assert rules_conflict(a, b) is not None
    assert rules_conflict(b, a) is not None
    assert rules_conflict(a, c) is None
    assert rules_conflict(a, other_domain) is None
    assert rules_conflict(a, a) is None


# ------------------------------------------------------------------------- lifecycle

GOOD_EVIDENCE = RuleEvidence(
    sample_count=40, validation_score=0.12, method="held_out_backtest", held_out=True
)


def test_every_status_has_a_declared_transition_set() -> None:
    assert set(TRANSITIONS) == set(RuleStatus)


def test_the_normal_path_is_draft_to_active_one_step_at_a_time() -> None:
    path = [
        RuleStatus.DRAFT,
        RuleStatus.PROPOSED,
        RuleStatus.TESTING,
        RuleStatus.VALIDATED,
        RuleStatus.ACTIVE,
        RuleStatus.DEPRECATED,
    ]
    for current, target in zip(path, path[1:], strict=False):
        assert check_transition(current, target, GOOD_EVIDENCE) == [], (current, target)


def test_a_new_rule_can_never_jump_to_active() -> None:
    for start in (RuleStatus.DRAFT, RuleStatus.PROPOSED, RuleStatus.TESTING):
        assert check_transition(start, RuleStatus.ACTIVE, GOOD_EVIDENCE)
    assert check_transition(RuleStatus.PROPOSED, RuleStatus.VALIDATED, GOOD_EVIDENCE)


def test_validation_needs_measured_held_out_evidence() -> None:
    weak = {
        "none": None,
        "empty": RuleEvidence(),
        "few": RuleEvidence(
            sample_count=5, validation_score=0.1, method="held_out_backtest", held_out=True
        ),
        "not held out": RuleEvidence(
            sample_count=50, validation_score=0.1, method="held_out_backtest", held_out=False
        ),
        "no method": RuleEvidence(sample_count=50, validation_score=0.1, held_out=True),
        "no score": RuleEvidence(sample_count=50, method="held_out_backtest", held_out=True),
    }
    for label, evidence in weak.items():
        assert check_transition(RuleStatus.TESTING, RuleStatus.VALIDATED, evidence), label


def test_rejection_is_possible_before_activation_and_final() -> None:
    for start in (RuleStatus.DRAFT, RuleStatus.PROPOSED, RuleStatus.TESTING, RuleStatus.VALIDATED):
        assert check_transition(start, RuleStatus.REJECTED, None) == []
    assert TRANSITIONS[RuleStatus.REJECTED] == frozenset()
    assert TRANSITIONS[RuleStatus.DEPRECATED] == frozenset()


def test_an_active_rule_can_be_rolled_back_and_nothing_resurrects_it() -> None:
    assert check_transition(RuleStatus.ACTIVE, RuleStatus.ROLLED_BACK, None) == []
    assert check_transition(RuleStatus.ROLLED_BACK, RuleStatus.ACTIVE, GOOD_EVIDENCE)
    assert check_transition(RuleStatus.DEPRECATED, RuleStatus.ACTIVE, GOOD_EVIDENCE)


def test_only_an_active_rule_is_operative() -> None:
    assert [s for s in RuleStatus if is_operative(s)] == [RuleStatus.ACTIVE]

"""Self-constraint engine (pure).

Level A (immutable, owner-defined): the closed list of action types, the protected names, the
direction of every limit and the "cannot override hard limits" flag. They live in code, are
never read from a rule, and a rule can only be accepted if it respects them.
Level B (adaptive): a rule may only make behaviour stricter, never looser.
Level C (preferences): operational preferences that cannot touch any safety setting.

The current limits are passed in (built from the live ``RiskConfig``) so that "stricter than
now" is always measured against the real values, never against a copy that could drift.
"""

import math
from dataclasses import dataclass

from ambre.cognitive.models import ActionType, RuleDefinition
from ambre.config.settings import RiskConfig

#: Limits that a rule may only lower (or keep).
LOWER_ONLY = frozenset({"max_position_fraction", "max_total_exposure_fraction", "kelly_fraction"})
#: Floors that a rule may only raise (or keep).
RAISE_ONLY = frozenset({"minimum_edge", "min_stake"})

#: Names no rule action may mention, in a key or in a value (matched case-insensitively).
PROTECTED_TOKENS = frozenset(
    {
        "execution_mode",
        "enable_manifold_orders",
        "manifold_api_key",
        "api_key",
        "secret",
        "password",
        "halted",
        "resume",
        "critical_drawdown",
        "failure_value_fraction",
    }
)

_LEVEL_B = frozenset(
    {
        ActionType.REDUCE_API_CALLS,
        ActionType.TIGHTEN_LIMIT,
        ActionType.RAISE_CONFIDENCE_THRESHOLD,
        ActionType.SUSPEND_STRATEGY,
        ActionType.ADD_OBSERVATION_PERIOD,
    }
)


@dataclass(frozen=True)
class CurrentLimits:
    max_position_fraction: float
    max_total_exposure_fraction: float
    kelly_fraction: float
    minimum_edge: float
    min_stake: float

    @classmethod
    def from_risk(cls, risk: RiskConfig) -> "CurrentLimits":
        return cls(
            max_position_fraction=risk.max_position_fraction,
            max_total_exposure_fraction=risk.max_total_exposure_fraction,
            kelly_fraction=risk.kelly_fraction,
            minimum_edge=risk.minimum_edge,
            min_stake=risk.min_stake,
        )


def level_of(action: ActionType) -> str:
    """ "B" = adaptive restriction, "C" = learned preference. Level A is never an action."""
    return "B" if action in _LEVEL_B else "C"


def _number(params: dict[str, object], key: str) -> float | None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _mentions_protected(definition: RuleDefinition) -> list[str]:
    hits: list[str] = []
    action = definition.action
    texts = [str(k) for k in action.params] + [str(v) for v in action.params.values()]
    for text in texts:
        lowered = text.lower()
        for token in sorted(PROTECTED_TOKENS):
            if token in lowered:
                hits.append(f"protected name {token!r} in the action")
    return sorted(set(hits))


def _expect(params: dict[str, object], allowed: set[str], required: set[str]) -> list[str]:
    reasons = [f"unexpected parameter {k!r}" for k in sorted(set(params) - allowed)]
    reasons += [f"missing parameter {k!r}" for k in sorted(required - set(params))]
    return reasons


def _text(params: dict[str, object], key: str) -> str | None:
    value = params.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _validate_params(definition: RuleDefinition, limits: CurrentLimits) -> list[str]:
    action = definition.action.type
    params: dict[str, object] = dict(definition.action.params)
    reasons: list[str]
    if action in (ActionType.PRIORITIZE_SOURCE, ActionType.DEPRIORITIZE_SOURCE):
        reasons = _expect(params, {"source_category"}, {"source_category"})
        if "source_category" in params and _text(params, "source_category") is None:
            reasons.append("source_category must be a non-empty text")
        return reasons
    if action is ActionType.PREFER_OFFICIAL_SOURCES:
        return _expect(params, set(), set())
    if action is ActionType.REDUCE_API_CALLS:
        reasons = _expect(params, {"provider_id", "factor"}, {"provider_id", "factor"})
        factor = _number(params, "factor")
        if "factor" in params and (factor is None or not 0 < factor <= 1):
            reasons.append("factor must be in (0, 1]: a rule may only reduce calls")
        if "provider_id" in params and _text(params, "provider_id") is None:
            reasons.append("provider_id must be a non-empty text")
        return reasons
    if action is ActionType.TIGHTEN_LIMIT:
        reasons = _expect(params, {"limit", "value"}, {"limit", "value"})
        name = _text(params, "limit")
        value = _number(params, "value")
        if "limit" in params and name not in LOWER_ONLY | RAISE_ONLY:
            reasons.append(f"limit {params.get('limit')!r} is not an adaptive limit")
            return reasons
        if "value" in params and (value is None or value <= 0):
            reasons.append("value must be a finite number above 0")
            return reasons
        if name is not None and value is not None:
            current = float(getattr(limits, name))
            if name in LOWER_ONLY and value > current:
                reasons.append(f"would loosen {name}: {value:g} > current {current:g}")
            if name in RAISE_ONLY and value < current:
                reasons.append(f"would loosen {name}: {value:g} < current {current:g}")
        return reasons
    if action is ActionType.RAISE_CONFIDENCE_THRESHOLD:
        reasons = _expect(params, {"threshold"}, {"threshold"})
        threshold = _number(params, "threshold")
        if "threshold" in params and (threshold is None or not 0 < threshold <= 1):
            reasons.append("threshold must be in (0, 1]")
        return reasons
    if action is ActionType.SUSPEND_STRATEGY:
        reasons = _expect(params, {"strategy"}, {"strategy"})
        if "strategy" in params and _text(params, "strategy") is None:
            reasons.append("strategy must be a non-empty text")
        return reasons
    if action is ActionType.ADD_OBSERVATION_PERIOD:
        reasons = _expect(params, {"seconds"}, {"seconds"})
        seconds = _number(params, "seconds")
        if "seconds" in params and (seconds is None or seconds <= 0 or seconds != int(seconds)):
            reasons.append("seconds must be a whole number above 0")
        return reasons
    return [f"unhandled action type {action.value}"]  # unreachable: the enum is closed


def validate_definition(definition: RuleDefinition, limits: CurrentLimits) -> list[str]:
    """Every reason a rule is structurally unacceptable. Empty list = acceptable *structurally*.

    This is not validation of usefulness: a rule that passes is still inactive until it has been
    tested on held-out data.
    """
    reasons: list[str] = []
    if not definition.restrictions.cannot_override_hard_limits:
        reasons.append("a rule must not waive the hard limits (cannot_override_hard_limits)")
    reasons += _mentions_protected(definition)
    reasons += _validate_params(definition, limits)
    return reasons


def rules_conflict(a: RuleDefinition, b: RuleDefinition) -> str | None:
    """A reason when two rules ask for opposite things in the same domain, else ``None``."""
    if a.domain != b.domain:
        return None
    pair = {a.action.type, b.action.type}
    if pair == {ActionType.PRIORITIZE_SOURCE, ActionType.DEPRIORITIZE_SOURCE} and (
        a.action.params.get("source_category") == b.action.params.get("source_category")
    ):
        return (
            f"opposite preferences for source category {a.action.params.get('source_category')!r}"
        )
    return None

"""Rule lifecycle (pure): the only legal moves between statuses and what each one requires.

DRAFT -> PROPOSED -> TESTING -> VALIDATED -> ACTIVE -> DEPRECATED, plus REJECTED (any time
before activation) and ROLLED_BACK (from ACTIVE). Nothing jumps over a step and nothing leaves a
final status. A rule only changes behaviour while it is ACTIVE.
"""

from ambre.cognitive.models import RuleEvidence, RuleStatus
from ambre.evaluation_engine.metrics import MIN_SAMPLE

S = RuleStatus

TRANSITIONS: dict[RuleStatus, frozenset[RuleStatus]] = {
    S.DRAFT: frozenset({S.PROPOSED, S.REJECTED}),
    S.PROPOSED: frozenset({S.TESTING, S.REJECTED}),
    S.TESTING: frozenset({S.VALIDATED, S.REJECTED}),
    S.VALIDATED: frozenset({S.ACTIVE, S.REJECTED}),
    S.ACTIVE: frozenset({S.DEPRECATED, S.ROLLED_BACK}),
    S.ROLLED_BACK: frozenset({S.DEPRECATED}),
    S.DEPRECATED: frozenset(),
    S.REJECTED: frozenset(),
}

MIN_VALIDATION_SAMPLES = MIN_SAMPLE


def is_operative(status: RuleStatus) -> bool:
    return status is RuleStatus.ACTIVE


def _evidence_reasons(evidence: RuleEvidence | None) -> list[str]:
    if evidence is None:
        return ["validation needs measured evidence"]
    reasons: list[str] = []
    if evidence.method is None:
        reasons.append("evidence has no measurement method")
    if evidence.held_out is not True:
        reasons.append("evidence was not measured on held-out data")
    if evidence.sample_count is None or evidence.sample_count < MIN_VALIDATION_SAMPLES:
        reasons.append(f"fewer than {MIN_VALIDATION_SAMPLES} validation samples")
    if evidence.validation_score is None:
        reasons.append("evidence has no validation score")
    return reasons


def check_transition(
    current: RuleStatus, target: RuleStatus, evidence: RuleEvidence | None
) -> list[str]:
    """Reasons the move is refused; an empty list means it is allowed."""
    if target not in TRANSITIONS[current]:
        return [f"{current.value} -> {target.value} is not a legal transition"]
    if target is RuleStatus.VALIDATED:
        return _evidence_reasons(evidence)
    if target is RuleStatus.ACTIVE:
        return _evidence_reasons(evidence)  # activation re-checks the evidence it relies on
    return []

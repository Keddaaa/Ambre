"""The information state of a market (pure): where it stands between "not looked at" and
"ready for a prediction".

One state per market, derived afresh after every pass from three independent facts: how the
market was understood (the plan), what happened to its search (the outcome) and whether a
validated model exists. Nothing is terminal except by the facts: a failed or deferred search is
retried, a missing source can become available, and evidence never implies a prediction.

RESEARCH_PENDING and RESEARCH_RUNNING are transient states of a pass; the states stored and
counted are the others.
"""

from dataclasses import dataclass
from enum import StrEnum

from ambre.intelligence.generic_planning import MarketPlan, PlanStatus


class MarketInfoState(StrEnum):
    NOT_ANALYZED = "NOT_ANALYZED"
    UNDERSTOOD = "UNDERSTOOD"
    RESEARCH_PLANNED = "RESEARCH_PLANNED"
    RESEARCH_PENDING = "RESEARCH_PENDING"
    RESEARCH_RUNNING = "RESEARCH_RUNNING"
    EVIDENCE_FOUND = "EVIDENCE_FOUND"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_AVAILABLE_SOURCE = "NO_AVAILABLE_SOURCE"
    RESEARCH_DEFERRED = "RESEARCH_DEFERRED"
    RESEARCH_FAILED = "RESEARCH_FAILED"
    READY_FOR_PREDICTION = "READY_FOR_PREDICTION"
    INSUFFICIENT_MODEL = "INSUFFICIENT_MODEL"


class Outcome(StrEnum):
    """What happened to the market's search group in this pass."""

    NOT_CONSIDERED = "NOT_CONSIDERED"
    SELECTED = "SELECTED"
    RUNNING = "RUNNING"
    OK = "OK"
    EMPTY = "EMPTY"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"
    FRESH = "FRESH"  # an earlier answer is still valid: nothing was asked again
    NO_PROVIDER = "NO_PROVIDER"
    NO_CONNECTOR = "NO_CONNECTOR"
    BUDGET_OR_QUEUE = "BUDGET_OR_QUEUE"
    LOW_VALUE = "LOW_VALUE"


@dataclass(frozen=True)
class StateDecision:
    state: MarketInfoState
    reason: str


S = MarketInfoState


def derive_state(
    plan: MarketPlan,
    outcome: Outcome,
    *,
    has_evidence: bool,
    model_available: bool,
    searchable: bool,
) -> StateDecision:
    """``searchable``: some enabled provider could serve this need if its budget allowed."""
    if plan.status is PlanStatus.NOT_APPLICABLE:
        return StateDecision(S.UNDERSTOOD, "market closed: waiting for its resolution")
    if plan.status in (PlanStatus.AMBIGUOUS, PlanStatus.OUT_OF_HORIZON):
        why = "; ".join(plan.blocking) or "question leaves a needed element open"
        return StateDecision(S.UNDERSTOOD, f"understood, not searchable yet: {why}")
    if plan.status is PlanStatus.NO_CONNECTOR:
        return StateDecision(S.NO_AVAILABLE_SOURCE, "no provider and no usable search topic")

    # COLLECTABLE from here
    if outcome is Outcome.NOT_CONSIDERED:
        return StateDecision(S.RESEARCH_PLANNED, "search planned, not yet considered by a pass")
    if outcome is Outcome.SELECTED:
        return StateDecision(S.RESEARCH_PENDING, "selected for this pass")
    if outcome is Outcome.RUNNING:
        return StateDecision(S.RESEARCH_RUNNING, "search in progress")
    if outcome in (Outcome.OK, Outcome.FRESH):
        if not has_evidence:
            return StateDecision(
                S.INSUFFICIENT_EVIDENCE, "searched: nothing relevant to this market"
            )
        if plan.estimable and model_available:
            return StateDecision(
                S.READY_FOR_PREDICTION, "evidence, clear question, validated model"
            )
        if plan.estimable:
            return StateDecision(S.INSUFFICIENT_MODEL, "evidence found but no validated model")
        return StateDecision(
            S.EVIDENCE_FOUND, "evidence found; the question still leaves things open"
        )
    if outcome is Outcome.EMPTY:
        return StateDecision(S.INSUFFICIENT_EVIDENCE, "searched: the source returned nothing")
    if outcome is Outcome.INCONCLUSIVE:
        return StateDecision(
            S.RESEARCH_DEFERRED, "the answer proved nothing either way: retry later"
        )
    if outcome is Outcome.FAILED:
        return StateDecision(S.RESEARCH_FAILED, "the search failed: it will be retried")
    if outcome is Outcome.NO_CONNECTOR:
        return StateDecision(S.NO_AVAILABLE_SOURCE, "provider enabled but no connector implemented")
    if outcome is Outcome.NO_PROVIDER:
        if searchable:
            return StateDecision(
                S.RESEARCH_DEFERRED, "provider unavailable now (budget, pause or stop)"
            )
        return StateDecision(S.NO_AVAILABLE_SOURCE, "no enabled provider can serve this need")
    if outcome is Outcome.LOW_VALUE:
        return StateDecision(
            S.RESEARCH_DEFERRED, "value below the threshold: not worth the quota now"
        )
    return StateDecision(S.RESEARCH_DEFERRED, "deferred by budget, pass limit or queue rank")

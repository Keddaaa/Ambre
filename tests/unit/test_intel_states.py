"""The twelve information states of a market and how each is derived (pure)."""

from datetime import UTC, datetime, timedelta

import pytest

from ambre.intelligence.generic_planning import MarketPlan, PlanStatus
from ambre.intelligence.planning import Gazetteer
from ambre.intelligence.resolution import analyze_market, load_rules
from ambre.intelligence.states import MarketInfoState, Outcome, derive_state

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
ANALYSIS = analyze_market(
    "m",
    "Will OpenAI release a new model before 2027?",
    NOW + timedelta(days=60),
    NOW,
    Gazetteer([]),
    load_rules(),
)


def plan(status: PlanStatus, blocking: tuple[str, ...] = ("open",)) -> MarketPlan:
    return MarketPlan(
        market_id="m", domain=ANALYSIS.domain, status=status, analysis=ANALYSIS, blocking=blocking
    )


def state(
    status: PlanStatus,
    outcome: Outcome = Outcome.NOT_CONSIDERED,
    *,
    evidence: bool = False,
    model: bool = False,
    searchable: bool = True,
    blocking: tuple[str, ...] = ("open",),
) -> MarketInfoState:
    return derive_state(
        plan(status, blocking),
        outcome,
        has_evidence=evidence,
        model_available=model,
        searchable=searchable,
    ).state


def test_the_twelve_states_exist() -> None:
    assert {s.value for s in MarketInfoState} == {
        "NOT_ANALYZED", "UNDERSTOOD", "RESEARCH_PLANNED", "RESEARCH_PENDING", "RESEARCH_RUNNING",
        "EVIDENCE_FOUND", "INSUFFICIENT_EVIDENCE", "NO_AVAILABLE_SOURCE", "RESEARCH_DEFERRED",
        "RESEARCH_FAILED", "READY_FOR_PREDICTION", "INSUFFICIENT_MODEL",
    }  # fmt: skip


def test_a_closed_or_ambiguous_market_is_understood_but_not_researched() -> None:
    assert state(PlanStatus.NOT_APPLICABLE) is MarketInfoState.UNDERSTOOD
    assert (
        state(PlanStatus.AMBIGUOUS, blocking=("event date not stated",))
        is MarketInfoState.UNDERSTOOD
    )
    assert state(PlanStatus.OUT_OF_HORIZON) is MarketInfoState.UNDERSTOOD


def test_no_way_to_search_means_no_available_source_not_a_dead_end_by_default() -> None:
    assert state(PlanStatus.NO_CONNECTOR) is MarketInfoState.NO_AVAILABLE_SOURCE


def test_a_collectable_market_waits_as_planned_until_it_is_considered() -> None:
    assert state(PlanStatus.COLLECTABLE) is MarketInfoState.RESEARCH_PLANNED
    assert state(PlanStatus.COLLECTABLE, Outcome.SELECTED) is MarketInfoState.RESEARCH_PENDING
    assert state(PlanStatus.COLLECTABLE, Outcome.RUNNING) is MarketInfoState.RESEARCH_RUNNING


def test_a_completed_search_with_evidence_is_evidence_found_until_a_model_exists() -> None:
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.OK, evidence=True) is MarketInfoState.EVIDENCE_FOUND
    )
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.FRESH, evidence=True)
        is MarketInfoState.EVIDENCE_FOUND
    )


def test_evidence_on_a_clear_market_without_a_model_is_insufficient_model() -> None:
    clear = state(PlanStatus.COLLECTABLE, Outcome.OK, evidence=True, model=False)
    assert clear is MarketInfoState.EVIDENCE_FOUND  # the question still leaves things open
    assert (
        derive_state(
            MarketPlan("m", ANALYSIS.domain, PlanStatus.COLLECTABLE, ANALYSIS),
            Outcome.OK, has_evidence=True, model_available=False, searchable=True,
        ).state
        is MarketInfoState.INSUFFICIENT_MODEL
    )  # fmt: skip


def test_ready_for_prediction_needs_evidence_a_clear_market_and_a_validated_model() -> None:
    clear = MarketPlan("m", ANALYSIS.domain, PlanStatus.COLLECTABLE, ANALYSIS)
    ready = derive_state(
        clear, Outcome.OK, has_evidence=True, model_available=True, searchable=True
    )
    assert ready.state is MarketInfoState.READY_FOR_PREDICTION
    for kwargs in (
        {"has_evidence": False, "model_available": True},
        {"has_evidence": True, "model_available": False},
    ):
        got = derive_state(clear, Outcome.OK, searchable=True, **kwargs).state  # type: ignore[arg-type]
        assert got is not MarketInfoState.READY_FOR_PREDICTION
    ambiguous = MarketPlan("m", ANALYSIS.domain, PlanStatus.COLLECTABLE, ANALYSIS, blocking=("x",))
    got = derive_state(
        ambiguous, Outcome.OK, has_evidence=True, model_available=True, searchable=True
    )
    assert got.state is MarketInfoState.EVIDENCE_FOUND


def test_a_search_with_nothing_relevant_is_insufficient_evidence_not_a_lost_opportunity() -> None:
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.OK, evidence=False)
        is MarketInfoState.INSUFFICIENT_EVIDENCE
    )
    assert state(PlanStatus.COLLECTABLE, Outcome.EMPTY) is MarketInfoState.INSUFFICIENT_EVIDENCE


def test_an_unusable_answer_or_a_busy_provider_defers_and_never_closes_the_market() -> None:
    for outcome in (Outcome.INCONCLUSIVE, Outcome.BUDGET_OR_QUEUE, Outcome.LOW_VALUE):
        assert state(PlanStatus.COLLECTABLE, outcome) is MarketInfoState.RESEARCH_DEFERRED
    assert state(PlanStatus.COLLECTABLE, Outcome.FAILED) is MarketInfoState.RESEARCH_FAILED


def test_no_provider_is_a_missing_source_only_when_no_provider_could_ever_serve() -> None:
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.NO_PROVIDER, searchable=False)
        is MarketInfoState.NO_AVAILABLE_SOURCE
    )
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.NO_PROVIDER, searchable=True)
        is MarketInfoState.RESEARCH_DEFERRED
    )
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.NO_CONNECTOR) is MarketInfoState.NO_AVAILABLE_SOURCE
    )


@pytest.mark.parametrize("outcome", [o for o in Outcome])
def test_every_decision_carries_a_reason(outcome: Outcome) -> None:
    d = derive_state(
        plan(PlanStatus.COLLECTABLE),
        outcome,
        has_evidence=False,
        model_available=False,
        searchable=True,
    )
    assert d.reason and d.state is not MarketInfoState.NOT_ANALYZED


def test_a_failed_or_deferred_state_is_recomputed_so_it_can_recover() -> None:
    assert state(PlanStatus.COLLECTABLE, Outcome.FAILED) is MarketInfoState.RESEARCH_FAILED
    assert (
        state(PlanStatus.COLLECTABLE, Outcome.OK, evidence=True) is MarketInfoState.EVIDENCE_FOUND
    )

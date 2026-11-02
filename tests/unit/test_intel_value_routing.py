"""Information Value Estimator and Smart Source Router (pure)."""

from datetime import UTC, datetime, timedelta

import pytest

from ambre.intelligence.budget import BudgetDecision
from ambre.intelligence.models import Domain, InfoNeed, ProviderCard, ProviderStatus, RateLimits
from ambre.intelligence.routing import ProviderHealth, route
from ambre.intelligence.value import estimate_value

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_a_settled_market_is_worth_little_and_an_open_one_more() -> None:
    near_certain = estimate_value(market_probability=0.01, relevance_prior=0.9)
    open_question = estimate_value(market_probability=0.5, relevance_prior=0.9)
    assert open_question.score > near_certain.score
    assert open_question.score == pytest.approx(0.9)  # 4*q*(1-q) = 1 at q = 0.5


def test_degenerate_probabilities_have_no_value() -> None:
    assert estimate_value(market_probability=0.0, relevance_prior=0.9).score == 0.0
    assert estimate_value(market_probability=1.0, relevance_prior=0.9).score == 0.0


def test_a_fresh_cached_answer_lowers_the_value_to_nothing_and_staleness_restores_it() -> None:
    fresh = estimate_value(
        market_probability=0.5,
        relevance_prior=1.0,
        cache_age_seconds=0.0,
        freshness_seconds=600,
    )
    half = estimate_value(
        market_probability=0.5,
        relevance_prior=1.0,
        cache_age_seconds=300.0,
        freshness_seconds=600,
    )
    stale = estimate_value(
        market_probability=0.5,
        relevance_prior=1.0,
        cache_age_seconds=6000.0,
        freshness_seconds=600,
    )
    assert fresh.score == 0.0
    assert half.score == pytest.approx(0.5)
    assert stale.score == pytest.approx(1.0)


def test_cost_divides_the_score() -> None:
    cheap = estimate_value(market_probability=0.5, relevance_prior=1.0, cost_units=1.0)
    dear = estimate_value(market_probability=0.5, relevance_prior=1.0, cost_units=4.0)
    assert dear.score == pytest.approx(cheap.score / 4)


def test_the_score_is_a_ranking_not_a_measured_gain() -> None:
    est = estimate_value(market_probability=0.3, relevance_prior=0.5)
    assert est.expected_information_gain is None
    assert set(est.components) == {
        "market_uncertainty",
        "relevance_prior",
        "staleness",
        "cost_units",
    }


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        estimate_value(market_probability=0.5, relevance_prior=0.5, cost_units=0.0)
    with pytest.raises(ValueError):
        estimate_value(market_probability=float("nan"), relevance_prior=0.5)


def card(
    pid: str, prior: float | None, enabled: bool = True, domain: Domain = Domain.SEISMIC
) -> ProviderCard:
    return ProviderCard(
        id=pid,
        name=pid,
        domain=domain,
        status=ProviderStatus.VERIFIED_FREE,
        enabled=enabled,
        internal_budget=RateLimits(per_day=10),
        capabilities={InfoNeed.RECENT_EARTHQUAKES: prior} if prior is not None else {},
    )


ALLOWED = BudgetDecision(True, "within budget")


def test_the_best_eligible_provider_is_chosen_and_the_others_explained() -> None:
    cards = [card("a_quakes", 0.9), card("b_quakes", 0.6), card("c_news", None, domain=Domain.NEWS)]
    d = route(
        InfoNeed.RECENT_EARTHQUAKES,
        cards,
        health={},
        budgets={"a_quakes": ALLOWED, "b_quakes": ALLOWED},
    )
    assert d.chosen == "a_quakes"
    by_id = {c.provider_id: c for c in d.candidates}
    assert by_id["a_quakes"].eligible and not by_id["b_quakes"].eligible
    assert any("lower relevance" in r for r in by_id["b_quakes"].reasons)
    assert not by_id["c_news"].eligible
    assert any("does not provide" in r for r in by_id["c_news"].reasons)
    assert "a_quakes" in d.explanation and "b_quakes" in d.explanation


def test_disabled_paused_and_over_budget_providers_are_rejected_with_a_reason() -> None:
    cards = [
        card("off", 0.99, enabled=False),
        card("paused", 0.95),
        card("broke", 0.9),
        card("ok", 0.5),
    ]
    d = route(
        InfoNeed.RECENT_EARTHQUAKES,
        cards,
        health={"paused": ProviderHealth(paused_until=NOW + timedelta(minutes=5))},
        budgets={
            "paused": ALLOWED,
            "broke": BudgetDecision(False, "day quota used (500/500)", 3600.0),
            "ok": ALLOWED,
        },
        now=NOW,
    )
    assert d.chosen == "ok"
    reasons = {c.provider_id: " ".join(c.reasons) for c in d.candidates}
    assert "disabled" in reasons["off"]
    assert "paused" in reasons["paused"]
    assert "day quota used" in reasons["broke"]


def test_no_eligible_provider_means_no_choice_and_says_why() -> None:
    d = route(InfoNeed.RECENT_EARTHQUAKES, [card("off", 0.9, enabled=False)], health={}, budgets={})
    assert d.chosen is None
    assert "no eligible provider" in d.explanation


def test_a_missing_budget_decision_is_a_refusal_not_a_free_pass() -> None:
    d = route(InfoNeed.RECENT_EARTHQUAKES, [card("a", 0.9)], health={}, budgets={})
    assert d.chosen is None
    assert any("no budget decision" in r for r in d.candidates[0].reasons)


def test_ties_are_broken_by_provider_id_so_routing_is_deterministic() -> None:
    cards = [card("zzz", 0.7), card("aaa", 0.7)]
    d = route(
        InfoNeed.RECENT_EARTHQUAKES, cards, health={}, budgets={"zzz": ALLOWED, "aaa": ALLOWED}
    )
    assert d.chosen == "aaa"

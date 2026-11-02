import pytest

from ambre.evaluation_engine.forward import (
    MIN_CLUSTERS,
    MIN_MARKETS,
    ForwardObservation,
    evaluate_forward,
)


def obs(
    i: int, q: float, p: float, outcome: bool, question: str | None = None
) -> ForwardObservation:
    return ForwardObservation(
        market_id=f"m{i}",
        question=question or f"Will alpha{i} beta{i} gamma{i} resolve?",
        market_probability=q,
        estimated_probability=p,
        outcome=outcome,
    )


def test_below_the_threshold_nothing_but_counts_is_shown_so_nobody_can_peek() -> None:
    few = [obs(i, 0.6, 0.5, i % 2 == 0) for i in range(MIN_MARKETS - 1)]
    r = evaluate_forward(few)
    assert r.status == "INSUFFICIENT_SAMPLE" and r.n == MIN_MARKETS - 1
    assert r.mean_brier_difference is None and r.ci95_clustered is None
    assert r.brier_market is None and r.brier_model is None
    assert str(MIN_MARKETS) in r.reason


def test_many_markets_but_few_distinct_questions_is_still_insufficient() -> None:
    same = [
        obs(i, 0.6, 0.5, i % 2 == 0, question="Will the shared event happen?") for i in range(400)
    ]
    r = evaluate_forward(same)
    assert r.n == 400 and r.n_clusters == 1 < MIN_CLUSTERS
    assert r.status == "INSUFFICIENT_SAMPLE" and r.ci95_clustered is None


def test_no_observation_is_an_empty_report_not_a_zero() -> None:
    r = evaluate_forward([])
    assert r.status == "INSUFFICIENT_SAMPLE" and r.n == 0 and r.mean_brier_difference is None


def test_a_model_that_really_is_closer_to_the_truth_is_confirmed() -> None:
    # price 0.6, truth about 0.48 (gap 0.12 -> outcomes at 0.4 here); the model says 0.48
    data = [obs(i, 0.6, 0.45, i % 5 in (0, 1)) for i in range(600)]  # YES rate 0.4
    r = evaluate_forward(data)
    assert r.status == "CONFIRMED"
    assert r.ci95_clustered is not None and r.ci95_clustered[1] < 0
    assert r.brier_model is not None and r.brier_market is not None
    assert r.brier_model < r.brier_market


def test_a_model_that_moves_the_wrong_way_is_refuted() -> None:
    data = [obs(i, 0.6, 0.75, i % 5 in (0, 1)) for i in range(600)]  # truth 0.4, model 0.75
    r = evaluate_forward(data)
    assert r.status == "REFUTED" and r.ci95_clustered is not None and r.ci95_clustered[0] > 0


def test_no_difference_is_not_confirmed_and_is_not_called_a_failure_either() -> None:
    data = [obs(i, 0.5, 0.5, i % 2 == 0) for i in range(600)]  # model equals the price
    r = evaluate_forward(data)
    assert r.status == "NOT_CONFIRMED" and r.mean_brier_difference == pytest.approx(0.0)


def test_one_comparison_means_the_plain_95_percent_interval_no_family_correction() -> None:
    data = [obs(i, 0.6, 0.55, i % 5 in (0, 1)) for i in range(600)]
    r = evaluate_forward(data)
    assert r.comparisons == 1 and r.z == pytest.approx(1.96, abs=0.001)

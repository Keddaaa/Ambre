from datetime import UTC, datetime, timedelta

import pytest

from ambre.config.settings import PredictionConfig
from ambre.evaluation_engine.historical import (
    HistoricalMarket,
    build_observations,
    evaluate_historical,
)
from ambre.evaluation_engine.history import PriceHistory, PricePoint

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CFG = PredictionConfig(shrinkage_market_weight=0.9)


def history(market_id: str, created: datetime, bets: list[tuple[float, float]]) -> PriceHistory:
    """bets = [(hours after creation, probability after)], opening at 0.5."""
    points = tuple(
        PricePoint(created + timedelta(hours=h), p, f"{market_id}-{i}")
        for i, (h, p) in enumerate(bets)
    )
    return PriceHistory(market_id, 0.5, points, complete=True)


def market(
    i: int,
    *,
    lifetime_days: float = 5,
    outcome: bool = True,
    bets: list[tuple[float, float]] | None = None,
    close_days: float | None = None,
    complete: bool = True,
    question: str | None = None,
) -> HistoricalMarket:
    created = T0 + timedelta(days=i)
    h = history(f"m{i}", created, bets if bets is not None else [(2.0, 0.6)])
    if not complete:
        h = PriceHistory(h.market_id, h.initial_probability, h.points, complete=False)
    return HistoricalMarket(
        market_id=f"m{i}",
        question=question or f"Will alpha{i} beta{i} gamma{i} resolve?",
        created_at=created,
        close_at=None if close_days is None else created + timedelta(days=close_days),
        resolution_at=created + timedelta(days=lifetime_days),
        outcome=outcome,
        history=h,
    )


# ------------------------------------------------------------------ observations


def test_a_decision_point_is_the_market_age_and_only_exists_if_the_market_was_still_open() -> None:
    obs, excluded = build_observations([market(0, lifetime_days=10)], (1, 7, 30))
    assert [(o.horizon_days, o.decision_at) for o in obs] == [
        (1, T0 + timedelta(days=1)),
        (7, T0 + timedelta(days=7)),
    ]
    assert excluded[30] == {"resolved_before_decision": 1}


def test_the_price_is_what_was_known_at_the_decision_instant_not_later() -> None:
    m = market(0, lifetime_days=10, bets=[(2.0, 0.60), (30.0, 0.95)])  # 0.95 is after day 1
    obs, _ = build_observations([m], (1,))
    assert obs[0].market_probability == 0.60
    late, _ = build_observations([m], (3,))
    assert late[0].market_probability == 0.95


def test_a_market_closed_before_the_decision_is_not_observed() -> None:
    obs, excluded = build_observations([market(0, lifetime_days=10, close_days=3)], (1, 7))
    assert [o.horizon_days for o in obs] == [1]
    assert excluded[7] == {"closed_before_decision": 1}


def test_incomplete_or_empty_histories_are_excluded_never_guessed() -> None:
    empty = HistoricalMarket(
        "m9", "q", T0, None, T0 + timedelta(days=9), True, PriceHistory("m9", None, (), True)
    )
    obs, excluded = build_observations([market(0, complete=False), empty], (1,))
    assert obs == []
    assert excluded[1] == {"history_incomplete": 1, "no_price_history": 1}


def test_observation_carries_the_outcome_and_resolution_for_later_chronology() -> None:
    obs, _ = build_observations([market(0, outcome=False, lifetime_days=4)], (1,))
    assert obs[0].outcome is False and obs[0].resolution_at == T0 + timedelta(days=4)


# ------------------------------------------------------------------ evaluation


def world(n: int = 90, *, calibrated: bool = True) -> list[HistoricalMarket]:
    """Markets resolving 5 days after creation; prices repeat 0.2 / 0.5 / 0.8; the outcome
    frequency equals the price when ``calibrated`` (exactly, by construction)."""
    prices = [0.2, 0.5, 0.8]
    yes_every = {0.2: 5, 0.5: 2, 0.8: 5}  # 1 in 5, 1 in 2, 4 in 5
    counts = {p: 0 for p in prices}
    out = []
    for i in range(n):
        p = prices[i % 3]
        k = counts[p]
        counts[p] += 1
        if p == 0.8:
            outcome = k % yes_every[p] != 0
        elif p == 0.2:
            outcome = k % yes_every[p] == 0
        else:
            outcome = k % 2 == 0
        if not calibrated:
            outcome = not outcome
        out.append(market(i, bets=[(1.0, p)], outcome=outcome))
    return out


def run(markets: list[HistoricalMarket], horizons: tuple[int, ...] = (1,)):  # type: ignore[no-untyped-def]
    obs, excluded = build_observations(markets, horizons)
    return evaluate_historical(obs, excluded, CFG)


def test_every_model_is_scored_on_the_same_markets() -> None:
    h = run(world()).horizons[0]
    assert h.n_common > 0
    sizes = {s.model: s.n for s in h.scores}
    assert set(sizes) == {"market_price", "ambre_baseline", "calibrated_price"}
    assert len(set(sizes.values())) == 1  # same population: scores are comparable


def test_chronology_the_first_decision_has_nothing_to_learn_from() -> None:
    h = run(world()).horizons[0]
    assert h.folds[0].n_train == 0
    # resolution is 5 days after creation and the decision is 1 day after: a market that
    # resolves exactly at another's decision is not known yet
    for fold in h.folds:
        known = [f for f in h.folds if f.resolution_at < fold.decision_at]
        assert fold.n_train == len(known)


def test_the_calibrator_abstains_until_it_has_enough_resolved_markets() -> None:
    h = run(world()).horizons[0]
    assert h.n_observations > h.n_common > 0  # early markets could not be calibrated
    assert all(f.calibrated is None for f in h.folds if f.n_train < 30)
    assert all(f.calibrated is not None for f in h.folds if f.n_train >= 30)


def test_a_late_outcome_never_changes_an_earlier_forecast() -> None:
    base = world(60)
    flipped = [*base[:-1], market(59, bets=[(1.0, 0.8)], outcome=not base[-1].outcome)]
    a = run(base).horizons[0].folds
    b = run(flipped).horizons[0].folds
    assert [f.baseline for f in a[:-1]] == [f.baseline for f in b[:-1]]
    assert [f.calibrated for f in a[:-1]] == [f.calibrated for f in b[:-1]]


def test_a_baseline_that_shrinks_towards_the_base_rate_does_worse_than_calibrated_prices() -> None:
    h = run(world()).horizons[0]
    cmp = {c.model: c for c in h.comparisons}
    base = cmp["ambre_baseline"]
    assert base.mean_brier_difference is not None and base.mean_brier_difference > 0
    assert base.verdict in ("worse", "inconclusive")  # never "better" here


def test_no_model_is_called_an_improvement_without_an_interval_that_excludes_zero() -> None:
    h = run(world()).horizons[0]
    for c in h.comparisons:
        if c.verdict == "better":
            assert c.ci95_clustered is not None and c.ci95_clustered[1] < 0
    assert all(c.verdict != "better" for c in h.comparisons)  # calibrated world: nothing to gain


def test_duplicated_questions_widen_the_interval_through_clustering() -> None:
    base = world(45)
    triples: list[HistoricalMarket] = []
    for j, m in enumerate(base):
        for k in range(3):  # three near-identical markets with the same question and outcome
            copy = market(
                j * 3 + k,
                bets=[(1.0, m.history.points[0].probability)],
                outcome=m.outcome,
                question=f"Will gamma{j} delta{j} epsilon{j} happen?",
            )
            triples.append(copy)
    h = run(triples).horizons[0]
    c = next(x for x in h.comparisons if x.model == "ambre_baseline")
    assert c.n_clusters is not None and c.n_clusters < h.n_common
    assert c.ci95_naive is not None and c.ci95_clustered is not None
    naive_width = c.ci95_naive[1] - c.ci95_naive[0]
    clustered_width = c.ci95_clustered[1] - c.ci95_clustered[0]
    assert clustered_width > naive_width


def test_the_context_model_is_reported_as_not_evaluated_with_the_reason() -> None:
    report = run(world())
    ctx = report.context_model
    assert ctx["status"] == "NOT_EVALUATED" and "point-in-time" in ctx["reason"]


def test_no_observation_means_no_metric_not_zero() -> None:
    report = run([])
    assert report.horizons[0].n_observations == 0
    assert all(s.brier is None and s.n == 0 for s in report.horizons[0].scores)
    assert report.horizons[0].comparisons == []


def test_horizons_are_evaluated_separately() -> None:
    report = run(world(), (1, 3))
    assert [h.horizon_days for h in report.horizons] == [1, 3]


def test_non_finite_or_out_of_range_prices_cannot_reach_the_scores() -> None:
    bad = HistoricalMarket(
        "mx",
        "q",
        T0,
        None,
        T0 + timedelta(days=5),
        True,
        PriceHistory("mx", 0.5, (PricePoint(T0 + timedelta(hours=1), 1.5, "b"),), True),
    )
    with pytest.raises(ValueError):
        build_observations([bad], (1,))


# ------------------------------------------------------------------ description of the whole sample


def test_the_whole_sample_calibration_describes_over_or_under_confidence() -> None:
    good = run(world()).horizons[0].full_sample_calibration
    assert good is not None and good["n"] == 90
    assert good["b"] == pytest.approx(1.0, abs=0.35)  # prices were built to be calibrated
    flipped = run(world(calibrated=False)).horizons[0].full_sample_calibration
    assert flipped is not None and flipped["b"] < 0  # prices point the wrong way


def test_the_whole_sample_calibration_is_a_description_not_a_forecast() -> None:
    h = run(world()).horizons[0]
    assert h.full_sample_calibration is not None
    assert "in-sample" in h.full_sample_calibration["note"]  # scores never use it


def test_too_few_markets_give_no_whole_sample_calibration() -> None:
    assert run(world(10)).horizons[0].full_sample_calibration is None


# ------------------------------------------------------------------ several comparisons, one report


def test_the_critical_value_grows_with_the_number_of_comparisons() -> None:
    from ambre.evaluation_engine.historical import family_z

    assert family_z(1) == pytest.approx(1.96, abs=0.001)
    assert family_z(8) == pytest.approx(2.734, abs=0.002)  # Bonferroni: 0.05 / 8 per comparison
    assert family_z(8) > family_z(2) > family_z(1)
    with pytest.raises(ValueError):
        family_z(0)


def test_an_interval_that_excludes_zero_alone_can_include_it_once_the_family_is_counted() -> None:
    from ambre.evaluation_engine.historical import cluster_interval, family_z

    diffs = [-0.02 if i % 2 else 0.0 for i in range(400)]  # mean -0.01
    clusters = list(range(400))
    alone, _ = cluster_interval(diffs, clusters, family_z(1))
    family, _ = cluster_interval(diffs, clusters, family_z(60))
    assert alone is not None and alone[1] < 0  # significant on its own
    assert family is not None and family[1] > alone[1]  # but wider once 60 are being tried


def test_the_verdict_uses_the_family_adjusted_interval_and_reports_both() -> None:
    h = run(world()).horizons[0]
    for c in h.comparisons:
        assert c.ci95_clustered is not None and c.ci_clustered_family is not None
        assert c.ci_clustered_family[0] <= c.ci95_clustered[0]
        assert c.ci_clustered_family[1] >= c.ci95_clustered[1]
        assert c.n_comparisons == 2  # two models against the market at one horizon


def test_the_report_says_how_stable_the_result_is_from_one_year_to_the_next() -> None:
    h = run(world(90)).horizons[0]
    assert [y.year for y in h.by_year] == sorted({y.year for y in h.by_year})
    assert sum(y.n for y in h.by_year) == h.n_common
    y = h.by_year[0]
    assert 0.0 <= y.mean_price <= 1.0 and 0.0 <= y.yes_rate <= 1.0
    assert y.brier_market is not None and y.brier_calibrated is not None

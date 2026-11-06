"""Chronological learning protocol (ADR-022), on synthetic markets."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from ambre.config.settings import PredictionConfig
from ambre.evaluation_engine.history import BetEvent, BetLevelMarket
from ambre.evaluation_engine.learning import (
    FULL,
    MARKET,
    PRICE_PATH,
    RECALIBRATED,
    LearningConfig,
    build_examples,
    chronological_split,
    run_learning,
)

T = datetime(2025, 7, 1, tzinfo=UTC)
END = datetime(2026, 5, 1, tzinfo=UTC)
CFG = LearningConfig(split_at=T, validation_end=END, horizons_days=(1, 3))


def market(
    market_id: str,
    created: datetime,
    resolved: datetime,
    *,
    question: str | None = None,
    outcome: bool = True,
    close: datetime | None = None,
    complete: bool = True,
    price: float = 0.6,
) -> BetLevelMarket:
    events = (
        BetEvent(
            f"{market_id}-1", created + timedelta(hours=1), 0.5, price, 10.0, f"u{market_id}", None
        ),
    )
    return BetLevelMarket(
        market_id=market_id,
        question=question or f"alpha{market_id} beta{market_id} gamma{market_id}",
        creator_id="c",
        created_at=created,
        close_at=close or resolved,
        resolution_at=resolved,
        outcome=outcome,
        complete=complete,
        bet_count=1,
        dropped=0,
        fetched_at=resolved + timedelta(days=1),
        events=events,
        final_probability=None,
    )


OLD = T - timedelta(days=100)


def test_each_market_is_on_one_side_at_most() -> None:
    split = chronological_split(
        [
            market("train", OLD, OLD + timedelta(days=10)),
            market("straddles", OLD, T + timedelta(days=10)),
            market("val", T + timedelta(days=1), T + timedelta(days=20)),
            market("late", END, END + timedelta(days=5)),
        ],
        CFG,
    )
    assert split.train_ids == {"train"}
    assert split.validation_ids == {"val"}
    assert split.unused == {"straddles_cutoff": 1, "after_validation": 1}


def test_a_market_resolved_exactly_at_the_cutoff_does_not_train() -> None:
    split = chronological_split([market("edge", OLD, T)], CFG)
    assert split.train_ids == set()


def test_the_same_event_cannot_sit_on_both_sides() -> None:
    q = "Will the Lakers win the 2026 championship title?"
    split = chronological_split(
        [
            market("a", OLD, OLD + timedelta(days=10), question=q),
            market("b", T + timedelta(days=1), T + timedelta(days=9), question=q + " "),
        ],
        CFG,
    )
    assert split.train_ids == set() and split.validation_ids == {"b"}
    assert split.purged_ids == {"a"}


def test_an_incomplete_read_is_used_nowhere() -> None:
    examples, excluded = build_examples(
        [market("m", OLD, OLD + timedelta(days=10), complete=False)], (1,)
    )
    assert examples == [] and excluded[1] == {"incomplete_read": 1}


def test_no_decision_point_after_the_resolution_or_the_close() -> None:
    created = OLD
    examples, excluded = build_examples(
        [
            market("quick", created, created + timedelta(hours=20)),
            market(
                "closed",
                created,
                created + timedelta(days=30),
                close=created + timedelta(hours=12),
            ),
            market("kept", created, created + timedelta(days=30)),
        ],
        (1,),
    )
    assert [x.market_id for x in examples] == ["kept"]
    assert excluded[1] == {"closed_before_decision": 1, "resolved_before_decision": 1}


def test_a_market_without_bets_has_no_example() -> None:
    m = market("m", OLD, OLD + timedelta(days=10))
    bare = BetLevelMarket(**{**m.__dict__, "events": ()})
    examples, excluded = build_examples([bare], (1,))
    assert examples == [] and excluded[1] == {"no_bet": 1}


def synthetic_universe(seed: int, signal: float) -> list[BetLevelMarket]:
    """Thin markets lie: when ``signal`` > 0, the price overstates YES unless bettors are many."""
    rng = random.Random(seed)
    out = []
    for i in range(1200):
        period = OLD - timedelta(days=rng.randint(1, 300)) if i < 800 else None
        created = period or T + timedelta(days=rng.randint(1, 250))
        resolved = created + timedelta(days=rng.randint(5, 40))
        if period and resolved >= T:
            resolved = T - timedelta(days=1)
        price = rng.uniform(0.1, 0.9)
        bets = rng.randint(1, 8)
        thin = bets <= 4
        p_yes = price * (1 - signal) if thin else price
        events = tuple(
            BetEvent(
                f"m{i}-{k}",
                created + timedelta(minutes=10 * (k + 1)),
                0.5 if k == 0 else price,
                price,
                5.0,
                f"u{i}-{k}",
                None,
            )
            for k in range(bets)
        )
        out.append(
            BetLevelMarket(
                market_id=f"m{i}",
                question=f"alpha{i} beta{i} gamma{i} delta{i}",
                creator_id=f"c{i % 40}",
                created_at=created,
                close_at=resolved,
                resolution_at=resolved,
                outcome=rng.random() < p_yes,
                complete=True,
                bet_count=bets,
                dropped=0,
                fetched_at=resolved + timedelta(days=1),
                events=events,
                final_probability=None,
            )
        )
    return out


def comparison(horizon: object, model: str, reference: str) -> object:
    return next(
        c
        for c in horizon.comparisons  # type: ignore[attr-defined]
        if c.model == model and c.reference == reference
    )


def test_a_planted_effect_is_found_out_of_sample() -> None:
    report = run_learning(synthetic_universe(1, 0.6), CFG, PredictionConfig())
    h = report.horizons[0]
    c = comparison(h, FULL, MARKET)
    assert c.mean_brier_difference < 0  # type: ignore[attr-defined]
    assert c.verdict == "better"  # type: ignore[attr-defined]
    assert FULL in h.candidates_for_shadow


def test_pure_noise_earns_no_candidate() -> None:
    report = run_learning(synthetic_universe(2, 0.0), CFG, PredictionConfig())
    assert all(h.candidates_for_shadow == [] for h in report.horizons)


def test_every_model_is_scored_on_exactly_the_same_markets() -> None:
    report = run_learning(synthetic_universe(3, 0.3), CFG, PredictionConfig())
    for h in report.horizons:
        assert len({s.n for s in h.scores}) == 1
        assert {s.model for s in h.scores} >= {MARKET, RECALIBRATED, PRICE_PATH, FULL}


def test_the_comparison_budget_is_the_pre_registered_one() -> None:
    cfg = LearningConfig(split_at=T, validation_end=END)
    report = run_learning(synthetic_universe(4, 0.3), cfg, PredictionConfig())
    assert report.n_comparisons == 24
    assert {c.n_comparisons for h in report.horizons for c in h.comparisons} == {24}


def test_outcomes_of_the_validation_cannot_change_the_fitted_models() -> None:
    universe = synthetic_universe(5, 0.3)
    flipped = [
        BetLevelMarket(**{**m.__dict__, "outcome": not m.outcome}) if m.created_at >= T else m
        for m in universe
    ]
    a = run_learning(universe, CFG, PredictionConfig())
    b = run_learning(flipped, CFG, PredictionConfig())
    assert [h.models for h in a.horizons] == [h.models for h in b.horizons]


def test_a_too_small_validation_gives_no_verdict() -> None:
    few = [market(f"m{i}", T + timedelta(days=i + 1), T + timedelta(days=40)) for i in range(5)]
    report = run_learning(few, CFG, PredictionConfig())
    assert all(h.candidates_for_shadow == [] for h in report.horizons)


def test_a_naive_cutoff_is_refused() -> None:
    with pytest.raises(ValueError):
        LearningConfig(split_at=datetime(2025, 7, 1), validation_end=END)

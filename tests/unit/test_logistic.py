"""Market-anchored logistic model (ADR-022)."""

import math
import random

import pytest

from ambre.prediction_engine.logistic import AnchoredLogistic, anchored_logit


def sample(n: int, seed: int = 1) -> tuple[list[dict[str, float | None]], list[float], list[bool]]:
    """YES depends on the price and on ``x``, not on ``noise``."""
    rng = random.Random(seed)
    rows: list[dict[str, float | None]] = []
    prices, outcomes = [], []
    for _ in range(n):
        q = rng.uniform(0.05, 0.95)
        x = rng.gauss(0, 1)
        z = anchored_logit(q) + 1.2 * x
        rows.append({"x": x, "noise": rng.gauss(0, 1)})
        prices.append(q)
        outcomes.append(rng.random() < 1 / (1 + math.exp(-z)))
    return rows, prices, outcomes


def test_an_enormous_penalty_gives_back_the_market_price() -> None:
    rows, prices, outcomes = sample(300)
    m = AnchoredLogistic.fit(rows, prices, outcomes, ("x", "noise"), l2=1e9)
    assert m is not None
    assert m.predict(rows[0], 0.3) == pytest.approx(0.3, abs=1e-3)


def test_a_real_signal_is_found_and_noise_stays_small() -> None:
    rows, prices, outcomes = sample(4000)
    m = AnchoredLogistic.fit(rows, prices, outcomes, ("x", "noise"), l2=1.0)
    assert m is not None
    w = dict(zip(m.names, m.weights, strict=True))
    assert w["x"] == pytest.approx(1.2, abs=0.2)
    assert abs(w["noise"]) < 0.15
    assert m.b == pytest.approx(1.0, abs=0.2)


def test_without_features_it_is_a_recalibration() -> None:
    rows, prices, outcomes = sample(500)
    m = AnchoredLogistic.fit(rows, prices, outcomes, (), l2=1.0)
    assert m is not None and m.names == () and m.weights == ()


def test_a_missing_value_takes_the_training_mean_and_says_so() -> None:
    rows, prices, outcomes = sample(500)
    rows[0]["x"] = None
    m = AnchoredLogistic.fit(rows, prices, outcomes, ("x",), l2=1.0)
    assert m is not None
    assert m.missing_rate["x"] == pytest.approx(1 / 500)
    assert m.predict({"x": None}, 0.4) == pytest.approx(m.predict({"x": m.means[0]}, 0.4))


def test_a_constant_feature_is_left_out() -> None:
    rows, prices, outcomes = sample(300)
    for r in rows:
        r["age"] = 3.0
    m = AnchoredLogistic.fit(rows, prices, outcomes, ("x", "age"), l2=1.0)
    assert m is not None and m.names == ("x",)


def test_too_small_a_sample_abstains() -> None:
    rows, prices, outcomes = sample(10)
    assert AnchoredLogistic.fit(rows, prices, outcomes, ("x",), l2=1.0, min_n=30) is None


def test_inputs_of_different_lengths_are_refused() -> None:
    rows, prices, outcomes = sample(40)
    with pytest.raises(ValueError):
        AnchoredLogistic.fit(rows, prices[:-1], outcomes, ("x",), l2=1.0)


def test_probabilities_stay_inside_zero_and_one_at_the_extremes() -> None:
    rows, prices, outcomes = sample(300)
    m = AnchoredLogistic.fit(rows, prices, outcomes, ("x",), l2=1.0)
    assert m is not None
    for q in (0.0, 0.001, 0.999, 1.0):
        assert 0.0 < m.predict({"x": 50.0}, q) < 1.0
    assert anchored_logit(0.0) == anchored_logit(0.001)


def test_a_separable_sample_does_not_blow_up() -> None:
    rows: list[dict[str, float | None]] = [{"x": float(i % 2)} for i in range(200)]
    outcomes = [i % 2 == 1 for i in range(200)]
    m = AnchoredLogistic.fit(rows, [0.5] * 200, outcomes, ("x",), l2=1.0)
    assert m is not None and all(math.isfinite(w) for w in m.weights)

import math

import pytest

from ambre.evaluation_engine.calibration import PlattCalibrator, logit


def _data(true_of_q: "callable", grid: list[float], reps: int) -> tuple[list[float], list[bool]]:  # type: ignore[valid-type]
    """Deterministic outcomes whose frequency at price q is exactly true_of_q(q)."""
    probs: list[float] = []
    outcomes: list[bool] = []
    for q in grid:
        yes = round(true_of_q(q) * reps)
        probs += [q] * reps
        outcomes += [True] * yes + [False] * (reps - yes)
    return probs, outcomes


GRID = [0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 0.95]


def test_it_abstains_below_the_minimum_sample_rather_than_fit_noise() -> None:
    assert PlattCalibrator.fit([0.2] * 29, [True] * 29, min_n=30) is None
    assert PlattCalibrator.fit([], [], min_n=30) is None


def test_prices_that_are_already_calibrated_stay_almost_unchanged() -> None:
    probs, outcomes = _data(lambda q: q, GRID, 100)
    cal = PlattCalibrator.fit(probs, outcomes)
    assert cal is not None
    assert cal.a == pytest.approx(0.0, abs=0.05) and cal.b == pytest.approx(1.0, abs=0.05)
    assert cal.predict(0.8) == pytest.approx(0.8, abs=0.02)


def test_overconfident_prices_are_pulled_towards_the_middle() -> None:
    probs, outcomes = _data(lambda q: 0.5 + 0.5 * (q - 0.5), GRID, 100)
    cal = PlattCalibrator.fit(probs, outcomes)
    assert cal is not None and cal.b < 1.0
    assert 0.5 < cal.predict(0.9) < 0.9 and 0.1 < cal.predict(0.1) < 0.5


def test_underconfident_prices_are_pushed_outwards() -> None:
    probs, outcomes = _data(lambda q: min(0.99, max(0.01, 0.5 + 1.4 * (q - 0.5))), GRID, 100)
    cal = PlattCalibrator.fit(probs, outcomes)
    assert cal is not None and cal.b > 1.0
    assert cal.predict(0.8) > 0.8


def test_few_observations_keep_the_fit_close_to_the_identity() -> None:
    probs, outcomes = _data(lambda q: 1.0 - q, GRID, 4)  # 36 points, perfectly inverted
    cal = PlattCalibrator.fit(probs, outcomes, min_n=30, l2=5.0)
    assert cal is not None
    assert abs(cal.b - 1.0) < 2.0  # the prior limits what 36 points can say


def test_every_prediction_is_a_strict_probability_even_at_the_edges() -> None:
    probs, outcomes = _data(lambda q: q, GRID, 20)
    cal = PlattCalibrator.fit(probs, outcomes)
    assert cal is not None
    for q in (0.0, 1e-9, 0.5, 1 - 1e-9, 1.0):
        assert 0.0 < cal.predict(q) < 1.0


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan, math.inf])
def test_out_of_range_probabilities_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        PlattCalibrator.fit([bad] * 40, [True] * 40)
    cal = PlattCalibrator(0.0, 1.0, 40)
    with pytest.raises(ValueError):
        cal.predict(bad)


def test_mismatched_lengths_are_rejected() -> None:
    with pytest.raises(ValueError):
        PlattCalibrator.fit([0.5] * 40, [True] * 39)


def test_logit_is_clipped_so_it_is_always_finite() -> None:
    assert math.isfinite(logit(0.0)) and math.isfinite(logit(1.0))
    assert logit(0.5) == 0.0

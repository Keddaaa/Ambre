import math

import pytest

from ambre.evaluation_engine.metrics import (
    ScoredForecast,
    brier_score,
    calibration_bins,
    evaluate,
    log_loss,
)


def test_brier_perfect_and_worst() -> None:
    assert brier_score([1.0, 0.0], [True, False]) == 0.0
    assert brier_score([0.0, 1.0], [True, False]) == 1.0


def test_brier_known_value() -> None:
    assert brier_score([0.8, 0.3], [True, False]) == pytest.approx((0.04 + 0.09) / 2)


def test_log_loss_known_value() -> None:
    assert log_loss([0.8], [True]) == pytest.approx(-math.log(0.8))
    assert log_loss([0.8], [False]) == pytest.approx(-math.log(0.2))


def test_log_loss_is_finite_on_certain_wrong_forecast() -> None:
    value = log_loss([0.0], [True])
    assert value is not None and math.isfinite(value)


def test_empty_input_gives_none_not_zero() -> None:
    assert brier_score([], []) is None
    assert log_loss([], []) is None
    assert calibration_bins([], []) == []


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan, math.inf])
def test_out_of_range_probability_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        brier_score([bad], [True])


def test_length_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError):
        brier_score([0.5, 0.5], [True])


def test_calibration_bins_only_report_non_empty_bins() -> None:
    bins = calibration_bins([0.05, 0.07, 0.95], [False, True, True], n_bins=10)
    assert [b.n for b in bins] == [2, 1]
    low, high = bins
    assert (low.lower, low.upper) == (0.0, 0.1)
    assert low.mean_forecast == pytest.approx(0.06)
    assert low.observed_rate == pytest.approx(0.5)
    assert high.observed_rate == 1.0


def test_probability_one_falls_in_last_bin() -> None:
    (only,) = calibration_bins([1.0], [True], n_bins=10)
    assert (only.lower, only.upper) == (0.9, 1.0)


def test_evaluate_empty_has_no_metrics_and_insufficient_sample() -> None:
    r = evaluate([])
    assert r.n == 0
    assert r.model_brier is None and r.market_brier is None
    assert r.brier_difference is None and r.brier_difference_ci95 is None
    assert r.sufficient_sample is False


def test_evaluate_identical_forecasts_gives_zero_difference() -> None:
    fs = [ScoredForecast(0.6, 0.6, True), ScoredForecast(0.4, 0.4, False)]
    r = evaluate(fs)
    assert r.brier_difference == pytest.approx(0.0)
    assert r.model_brier == pytest.approx(r.market_brier)


def test_evaluate_negative_difference_means_model_better() -> None:
    fs = [ScoredForecast(0.5, 0.9, True) for _ in range(40)]
    r = evaluate(fs)
    assert r.brier_difference is not None and r.brier_difference < 0
    assert r.sufficient_sample is True


def test_single_forecast_has_no_confidence_interval() -> None:
    r = evaluate([ScoredForecast(0.5, 0.9, True)])
    assert r.brier_difference is not None
    assert r.brier_difference_ci95 is None


def test_confidence_interval_brackets_the_difference() -> None:
    fs = [ScoredForecast(0.5, 0.7 if i % 2 else 0.4, i % 3 == 0) for i in range(60)]
    r = evaluate(fs)
    assert r.brier_difference is not None and r.brier_difference_ci95 is not None
    lo, hi = r.brier_difference_ci95
    assert lo <= r.brier_difference <= hi

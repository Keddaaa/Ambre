import math
from datetime import UTC, datetime

import pytest

from ambre.config.settings import PredictionConfig, RecalibrationConfig
from ambre.feature_engine.features import FeatureVector
from ambre.prediction_engine.calibration import PlattCalibrator
from ambre.prediction_engine.recalibration import RecalibratedPrice

DAY = 86_400.0
NOW = datetime(2026, 11, 3, tzinfo=UTC)
CALIBRATION = RecalibrationConfig(
    a=-0.318,
    b=0.937,
    n_fit=4474,
    fitted_on="2026-11-02, markets resolved 2023-2026, 3 days after creation",
    min_age_days=2.0,
    max_age_days=4.0,
    uncertainty=0.03,
)
MODEL = RecalibratedPrice(CALIBRATION, PredictionConfig())


def features(q: float | None, age_days: float | None) -> FeatureVector:
    return FeatureVector(
        market_id="m1",
        available_at=NOW,
        values={
            "market_probability": q,
            "seconds_since_creation": None if age_days is None else age_days * DAY,
        },
    )


def test_inside_the_window_the_price_is_shifted_by_the_frozen_logistic() -> None:
    est = MODEL.predict(features(0.65, 3.0))
    expected = PlattCalibrator(-0.318, 0.937, 4474).predict(0.65)
    assert est.probability == pytest.approx(expected, abs=1e-12)
    assert est.probability < 0.65  # prices of that age were too high
    assert est.uncertainty == 0.03
    assert est.inputs["in_window"] == 1 and est.inputs["market_probability"] == 0.65


@pytest.mark.parametrize("age", [0.1, 1.99, 4.01, 20.0])
def test_outside_the_window_it_echoes_the_market_so_no_edge_exists(age: float) -> None:
    est = MODEL.predict(features(0.65, age))
    assert est.probability == 0.65 and est.inputs["in_window"] == 0
    assert est.uncertainty == PredictionConfig().uncertainty_floor


@pytest.mark.parametrize("age", [2.0, 4.0])
def test_the_window_bounds_are_inclusive(age: float) -> None:
    assert MODEL.predict(features(0.65, age)).inputs["in_window"] == 1


def test_an_unknown_age_is_never_assumed_to_be_in_the_window() -> None:
    est = MODEL.predict(features(0.65, None))
    assert est.probability == 0.65 and est.inputs["in_window"] == 0


def test_a_missing_market_price_is_an_error_not_a_default() -> None:
    with pytest.raises(ValueError):
        MODEL.predict(features(None, 3.0))


@pytest.mark.parametrize("q", [0.0, 1.0, 1e-9, 1 - 1e-9])
def test_extreme_prices_stay_strict_probabilities(q: float) -> None:
    est = MODEL.predict(features(q, 3.0))
    assert 0.0 < est.probability < 1.0 and math.isfinite(est.probability)


def test_the_model_is_frozen_the_same_inputs_always_give_the_same_estimate() -> None:
    assert MODEL.predict(features(0.4, 3.0)) == MODEL.predict(features(0.4, 3.0))


def test_the_version_names_every_frozen_parameter_so_a_decision_can_be_traced() -> None:
    v = MODEL.version
    assert "recalibrated-price" in v and "-0.318" in v and "0.937" in v and "2-4d" in v


def test_a_price_equal_to_the_calibrated_value_gives_no_shift_with_the_identity() -> None:
    identity = RecalibratedPrice(
        RecalibrationConfig(
            a=0.0, b=1.0, n_fit=100, fitted_on="test", min_age_days=0, max_age_days=9
        ),
        PredictionConfig(),
    )
    assert identity.predict(features(0.42, 3.0)).probability == pytest.approx(0.42, abs=1e-9)

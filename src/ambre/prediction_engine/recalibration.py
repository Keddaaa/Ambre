"""Recalibrated market price (pure, no I/O): a model written down before it is tested.

The historical evaluation (ADR-018) found that Manifold prices ran a few points too high for
markets a few days old. This model applies that finding as a *frozen* logistic shift,
``P(YES) = sigmoid(a + b * logit(q))``, to markets whose age is inside a fixed window, and echoes
the market price everywhere else, where nothing was measured. Echoing means the estimate equals
the price, so the strategy sees no edge and does not trade.

The parameters come from configuration and are never refitted here. That is the point of a
forward test: the rule, its window and its uncertainty are fixed in advance and the markets it is
judged on have not happened yet.
"""

from ambre.config.settings import PredictionConfig, RecalibrationConfig
from ambre.domain.models import Estimate
from ambre.feature_engine.features import FeatureVector
from ambre.prediction_engine.calibration import PlattCalibrator

_DAY = 86_400.0


class RecalibratedPrice:
    def __init__(self, calibration: RecalibrationConfig, cfg: PredictionConfig) -> None:
        self._cal = calibration
        self._calibrator = PlattCalibrator(calibration.a, calibration.b, calibration.n_fit)
        self._floor = cfg.uncertainty_floor
        self.version = (
            f"recalibrated-price-v1(a={calibration.a:g},b={calibration.b:g},"
            f"age={calibration.min_age_days:g}-{calibration.max_age_days:g}d,n={calibration.n_fit})"
        )

    def _in_window(self, f: FeatureVector) -> bool:
        age = f.values.get("seconds_since_creation")
        if age is None:
            return False  # an unknown age is never assumed to be inside the window
        return self._cal.min_age_days * _DAY <= float(age) <= self._cal.max_age_days * _DAY

    def predict(self, f: FeatureVector) -> Estimate:
        q = f.values.get("market_probability")
        if q is None:
            raise ValueError(f"market_probability missing for market {f.market_id!r}")
        q = float(q)
        if not self._in_window(f):
            return Estimate(
                probability=q,
                uncertainty=self._floor,
                model_version=self.version,
                inputs={"market_probability": q, "in_window": 0},
            )
        return Estimate(
            probability=self._calibrator.predict(q),
            uncertainty=self._cal.uncertainty,
            model_version=self.version,
            inputs={
                "market_probability": q,
                "in_window": 1,
                "a": self._cal.a,
                "b": self._cal.b,
                "n_fit": self._cal.n_fit,
            },
        )

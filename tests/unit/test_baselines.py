import math

import pytest
from builders import make_feature_vector

from ambre.config.settings import PredictionConfig
from ambre.prediction_engine.baselines import BaseRateShrinkage, MarketPriceBaseline


def test_market_price_baseline():
    m = MarketPriceBaseline(PredictionConfig())
    e = m.predict(make_feature_vector(q=0.37))
    assert m.version == "market-price-v0.1"
    assert e.model_version == "market-price-v0.1"
    assert e.probability == pytest.approx(0.37)
    assert e.uncertainty == pytest.approx(0.02)


def test_base_rate_fit_and_predict():
    m = BaseRateShrinkage.fit(["YES"] * 3 + ["NO"] * 7, PredictionConfig())
    e = m.predict(make_feature_vector(q=0.5))
    assert e.probability == pytest.approx(0.9 * 0.5 + 0.1 * (4 / 12))
    rate = 4 / 12
    rate_sd = math.sqrt(rate * (1 - rate) / 13)
    assert e.uncertainty == pytest.approx(math.sqrt((0.1 * rate_sd) ** 2 + 0.02**2))
    assert e.model_version == "base-rate-shrinkage-v0.1(w=0.9,n=10)"
    assert e.inputs["rate"] == pytest.approx(rate)
    assert e.inputs["n"] == 10
    assert e.inputs["w"] == 0.9


def test_base_rate_ignores_non_binary_outcomes():
    m = BaseRateShrinkage.fit(["YES", "NO", "CANCEL", "MKT"], PredictionConfig())
    assert m.n == 2
    assert m.rate == pytest.approx(0.5)


def test_base_rate_empty_history_is_uniform():
    m = BaseRateShrinkage.fit([], PredictionConfig())
    e = m.predict(make_feature_vector(q=0.8))
    assert m.rate == 0.5
    assert m.n == 0
    assert e.probability == pytest.approx(0.9 * 0.8 + 0.1 * 0.5)
    assert e.uncertainty > 0.02


def test_predict_requires_market_probability():
    fv = make_feature_vector(q=0.5)
    fv.values["market_probability"] = None
    with pytest.raises(ValueError):
        MarketPriceBaseline(PredictionConfig()).predict(fv)

"""Probability baselines (pure, no I/O)."""

import math
from collections.abc import Sequence
from typing import Protocol

from ambre.config.settings import PredictionConfig
from ambre.domain.models import Estimate
from ambre.feature_engine.features import FeatureVector


class ProbabilityModel(Protocol):
    version: str

    def predict(self, f: FeatureVector) -> Estimate: ...


def _market_probability(f: FeatureVector) -> float:
    q = f.values.get("market_probability")
    if q is None:
        raise ValueError(f"market_probability missing for market {f.market_id!r}")
    return float(q)


class MarketPriceBaseline:
    """Echoes the market price: by construction it never finds an edge."""

    version = "market-price-v0.1"

    def __init__(self, cfg: PredictionConfig) -> None:
        self._floor = cfg.uncertainty_floor

    def predict(self, f: FeatureVector) -> Estimate:
        q = _market_probability(f)
        return Estimate(
            probability=q,
            uncertainty=self._floor,
            model_version=self.version,
            inputs={"market_probability": q},
        )


class BaseRateShrinkage:
    """Shrinks the market price towards the historical YES base rate.

    Unvalidated estimator: it is used to produce *theoretical* opportunities and
    makes no claim of an edge over the market (ADR-004).

    The base rate is the beta-binomial posterior mean ``(yes+1)/(n+2)`` over
    resolved YES/NO outcomes; other resolutions (CANCEL, MKT, ...) are ignored.
    """

    def __init__(self, rate: float, rate_sd: float, n: int, cfg: PredictionConfig) -> None:
        self.rate = rate
        self.rate_sd = rate_sd
        self.n = n
        self.w = cfg.shrinkage_market_weight
        self._floor = cfg.uncertainty_floor
        self.version = f"base-rate-shrinkage-v0.1(w={self.w},n={n})"

    @classmethod
    def fit(cls, outcomes: Sequence[str], cfg: PredictionConfig) -> "BaseRateShrinkage":
        yes = sum(1 for o in outcomes if o == "YES")
        n = yes + sum(1 for o in outcomes if o == "NO")
        rate = (yes + 1) / (n + 2)
        rate_sd = math.sqrt(rate * (1 - rate) / (n + 3))
        return cls(rate, rate_sd, n, cfg)

    def predict(self, f: FeatureVector) -> Estimate:
        q = _market_probability(f)
        w = self.w
        return Estimate(
            probability=w * q + (1 - w) * self.rate,
            uncertainty=math.sqrt(((1 - w) * self.rate_sd) ** 2 + self._floor**2),
            model_version=self.version,
            inputs={"market_probability": q, "rate": self.rate, "n": self.n, "w": w},
        )

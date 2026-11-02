"""Information Value Estimator, version 1: a deterministic ranking (pure).

``expected_information_gain`` stays ``None``: nothing here is a measured gain. The score only
orders searches and feeds the stop rule. It is calibrated against the real change in Brier
score with and without the source in a later phase (docs/INTELLIGENCE_ARCHITECTURE.md).

    score = relevance_prior * market_uncertainty * staleness / cost_units

- ``market_uncertainty`` = 4 q (1 - q): a market already priced near 0 or 1 gains little.
- ``staleness`` = 1 with no cached answer, else min(1, age / freshness): a fresh, valid answer
  is not searched again.
- ``relevance_prior`` is an a-priori weight from the registry, not a measurement.
"""

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValueEstimate:
    score: float
    components: dict[str, float] = field(default_factory=dict)
    expected_information_gain: None = None


def estimate_value(
    *,
    market_probability: float,
    relevance_prior: float,
    cache_age_seconds: float | None = None,
    freshness_seconds: float | None = None,
    cost_units: float = 1.0,
) -> ValueEstimate:
    if not math.isfinite(market_probability):
        raise ValueError("market_probability must be finite")
    if not cost_units > 0:
        raise ValueError("cost_units must be positive")
    q = market_probability
    uncertainty = 4 * q * (1 - q) if 0.0 < q < 1.0 else 0.0
    if cache_age_seconds is None or freshness_seconds is None:
        staleness = 1.0
    else:
        staleness = min(1.0, max(0.0, cache_age_seconds) / freshness_seconds)
    score = relevance_prior * uncertainty * staleness / cost_units
    return ValueEstimate(
        score=score,
        components={
            "market_uncertainty": uncertainty,
            "relevance_prior": relevance_prior,
            "staleness": staleness,
            "cost_units": cost_units,
        },
    )

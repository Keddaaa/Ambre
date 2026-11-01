"""Forecast evaluation metrics (pure, no I/O).

Everything here describes *measured* quality on resolved markets. A missing
value is ``None``, never 0, and no verdict is issued on a small sample.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

LOG_LOSS_EPS = 1e-6
DEFAULT_BINS = 10
MIN_SAMPLE = 30
_Z95 = 1.96


@dataclass(frozen=True)
class ScoredForecast:
    """One resolved YES/NO market: the price and the model estimate at decision time."""

    market_probability: float
    estimated_probability: float
    outcome: bool


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    n: int
    mean_forecast: float
    observed_rate: float


@dataclass(frozen=True)
class EvaluationReport:
    n: int
    sufficient_sample: bool
    min_sample: int
    model_brier: float | None
    market_brier: float | None
    model_log_loss: float | None
    market_log_loss: float | None
    #: model Brier minus market Brier; negative means the model scored better.
    brier_difference: float | None
    #: Normal-approximation 95% interval of the paired difference; None below 2 samples.
    brier_difference_ci95: tuple[float, float] | None
    model_calibration: list[CalibrationBin]
    market_calibration: list[CalibrationBin]


def _check(probs: Sequence[float], outcomes: Sequence[bool]) -> None:
    if len(probs) != len(outcomes):
        raise ValueError("probabilities and outcomes must have the same length")
    for p in probs:
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise ValueError(f"probability out of range: {p!r}")


def brier_score(probs: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    _check(probs, outcomes)
    if not probs:
        return None
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(
        probs
    )


def log_loss(probs: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    """Mean negative log-likelihood; probabilities are clipped to ``[eps, 1-eps]``."""
    _check(probs, outcomes)
    if not probs:
        return None
    total = 0.0
    for p, o in zip(probs, outcomes, strict=True):
        q = min(max(p, LOG_LOSS_EPS), 1.0 - LOG_LOSS_EPS)
        total -= math.log(q if o else 1.0 - q)
    return total / len(probs)


def calibration_bins(
    probs: Sequence[float], outcomes: Sequence[bool], n_bins: int = DEFAULT_BINS
) -> list[CalibrationBin]:
    """Equal-width bins over [0, 1]; empty bins are omitted, p == 1 goes in the last bin."""
    _check(probs, outcomes)
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    buckets: dict[int, list[tuple[float, bool]]] = {}
    for p, o in zip(probs, outcomes, strict=True):
        index = min(int(p * n_bins), n_bins - 1)
        buckets.setdefault(index, []).append((p, o))
    result = []
    for index in sorted(buckets):
        items = buckets[index]
        result.append(
            CalibrationBin(
                lower=round(index / n_bins, 10),
                upper=round((index + 1) / n_bins, 10),
                n=len(items),
                mean_forecast=sum(p for p, _ in items) / len(items),
                observed_rate=sum(1 for _, o in items if o) / len(items),
            )
        )
    return result


def _paired_ci(diffs: Sequence[float]) -> tuple[float, float] | None:
    n = len(diffs)
    if n < 2:
        return None
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    half = _Z95 * math.sqrt(var / n)
    return (mean - half, mean + half)


def evaluate(forecasts: Sequence[ScoredForecast], min_sample: int = MIN_SAMPLE) -> EvaluationReport:
    model = [f.estimated_probability for f in forecasts]
    market = [f.market_probability for f in forecasts]
    outcomes = [f.outcome for f in forecasts]
    model_brier = brier_score(model, outcomes)
    market_brier = brier_score(market, outcomes)
    diff = None
    if model_brier is not None and market_brier is not None:
        diff = model_brier - market_brier
    squared_diffs = [
        (m - (1.0 if o else 0.0)) ** 2 - (q - (1.0 if o else 0.0)) ** 2
        for m, q, o in zip(model, market, outcomes, strict=True)
    ]
    return EvaluationReport(
        n=len(forecasts),
        sufficient_sample=len(forecasts) >= min_sample,
        min_sample=min_sample,
        model_brier=model_brier,
        market_brier=market_brier,
        model_log_loss=log_loss(model, outcomes),
        market_log_loss=log_loss(market, outcomes),
        brier_difference=diff,
        brier_difference_ci95=_paired_ci(squared_diffs),
        model_calibration=calibration_bins(model, outcomes),
        market_calibration=calibration_bins(market, outcomes),
    )

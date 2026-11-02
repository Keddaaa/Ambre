"""Point-in-time historical evaluation on resolved markets (pure, no I/O).

Goal: measure, on markets that are already resolved, whether any forecast beats the market price,
without letting the future leak into the past.

* A decision point is a market *age* (``horizon_days`` after creation). It exists only if the market
  was still open (not resolved, not closed) at that instant, and the price is rebuilt from the bets
  made up to it (``history.price_at``). Nothing later is read. Choosing the instant from the age
  rather than from the resolution date avoids selecting markets by how they ended; the price of
  that choice is that quick markets have no long-horizon point, so each horizon is evaluated
  separately and its population is reported.
* Models are fitted in chronological order, each only on markets whose resolution was known
  strictly before the decision instant, and never on the market being forecast.
* Three forecasts are compared on exactly the same markets: the market price, the AMBRE
  base-rate baseline and a recalibrated market price. Contextual (information-source) models are
  *not* evaluated: no point-in-time contextual feature exists for markets that resolved before the
  feature journal began, and none is manufactured.
* The uncertainty of each paired difference is reported twice: the naive interval (markets
  treated as independent) and a cluster-robust one, where markets whose questions are almost the
  same are one cluster, because they are not independent evidence.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ambre.config.settings import PredictionConfig
from ambre.domain.similarity import cluster_texts
from ambre.evaluation_engine.calibration import DEFAULT_MIN_N, PlattCalibrator
from ambre.evaluation_engine.history import PriceHistory, price_at
from ambre.evaluation_engine.metrics import CalibrationBin, brier_score, calibration_bins, log_loss
from ambre.feature_engine.features import FeatureVector
from ambre.prediction_engine.baselines import BaseRateShrinkage

DEFAULT_HORIZONS_DAYS = (1, 3, 7, 30)
MARKET = "market_price"
BASELINE = "ambre_baseline"
CALIBRATED = "calibrated_price"
MIN_CLUSTERS_FOR_VERDICT = 30
_Z95 = 1.96


@dataclass(frozen=True)
class HistoricalMarket:
    market_id: str
    question: str
    created_at: datetime
    close_at: datetime | None
    resolution_at: datetime
    outcome: bool
    history: PriceHistory


@dataclass(frozen=True)
class HistoricalObservation:
    market_id: str
    question: str
    horizon_days: int
    decision_at: datetime
    market_probability: float
    resolution_at: datetime
    outcome: bool


@dataclass(frozen=True)
class Fold:
    market_id: str
    horizon_days: int
    decision_at: datetime
    resolution_at: datetime
    n_train: int
    market: float
    baseline: float
    #: None while the calibrator has too few resolved markets to be fitted
    calibrated: float | None
    outcome: bool


@dataclass(frozen=True)
class ModelScore:
    model: str
    n: int
    brier: float | None
    log_loss: float | None
    calibration: list[CalibrationBin]


@dataclass(frozen=True)
class PairedComparison:
    """``model`` against the market price: a positive Brier difference means the model is worse."""

    model: str
    n: int
    n_clusters: int | None
    mean_brier_difference: float | None
    ci95_naive: tuple[float, float] | None
    ci95_clustered: tuple[float, float] | None
    verdict: str  # better | worse | inconclusive


@dataclass(frozen=True)
class HorizonReport:
    horizon_days: int
    n_observations: int
    n_common: int
    exclusions: dict[str, int]
    folds: list[Fold]
    scores: list[ModelScore]
    comparisons: list[PairedComparison]
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HistoricalReport:
    horizons: list[HorizonReport]
    context_model: dict[str, str]
    method: dict[str, str]


# --------------------------------------------------------------------------- observations


def build_observations(
    markets: Sequence[HistoricalMarket], horizons_days: Sequence[int] = DEFAULT_HORIZONS_DAYS
) -> tuple[list[HistoricalObservation], dict[int, dict[str, int]]]:
    """One observation per market and horizon, plus how many were excluded and why."""
    observations: list[HistoricalObservation] = []
    excluded: dict[int, dict[str, int]] = {h: {} for h in horizons_days}

    def skip(horizon: int, reason: str) -> None:
        excluded[horizon][reason] = excluded[horizon].get(reason, 0) + 1

    for m in markets:
        for horizon in horizons_days:
            decision_at = m.created_at + timedelta(days=horizon)
            if decision_at >= m.resolution_at:
                skip(horizon, "resolved_before_decision")
                continue
            if m.close_at is not None and decision_at >= m.close_at:
                skip(horizon, "closed_before_decision")
                continue
            if not m.history.complete:
                skip(horizon, "history_incomplete")
                continue
            price = price_at(m.history, decision_at)
            if price is None:
                skip(horizon, "no_price_history")
                continue
            if not math.isfinite(price) or not 0.0 <= price <= 1.0:
                raise ValueError(f"price out of range for {m.market_id}: {price!r}")
            observations.append(
                HistoricalObservation(
                    market_id=m.market_id,
                    question=m.question,
                    horizon_days=horizon,
                    decision_at=decision_at,
                    market_probability=price,
                    resolution_at=m.resolution_at,
                    outcome=m.outcome,
                )
            )
    return observations, {h: dict(sorted(r.items())) for h, r in excluded.items()}


# --------------------------------------------------------------------------- statistics


def _naive_ci(diffs: Sequence[float]) -> tuple[float, float] | None:
    """Mean of the paired differences +/- 1.96 standard errors, the standard error being
    ``s / sqrt(n)`` with ``s`` the sample (n-1) standard deviation; markets assumed independent."""
    n = len(diffs)
    if n < 2:
        return None
    mean = sum(diffs) / n
    s2 = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    half = _Z95 * math.sqrt(s2 / n)
    return (mean - half, mean + half)


def _cluster_ci(
    diffs: Sequence[float], clusters: Sequence[int]
) -> tuple[tuple[float, float] | None, int]:
    """Cluster-robust interval of the mean (CR1): the residuals of one cluster are summed before
    squaring, so near-duplicate markets count as one piece of evidence, not several."""
    n = len(diffs)
    groups: dict[int, float] = defaultdict(float)
    mean = sum(diffs) / n if n else 0.0
    for d, c in zip(diffs, clusters, strict=True):
        groups[c] += d - mean
    g = len(groups)
    if g < 2:
        return None, g
    variance = (g / (g - 1)) * sum(s * s for s in groups.values()) / (n * n)
    half = _Z95 * math.sqrt(variance)
    return (mean - half, mean + half), g


def _verdict(ci: tuple[float, float] | None, n_clusters: int) -> str:
    if ci is None or n_clusters < MIN_CLUSTERS_FOR_VERDICT:
        return "inconclusive"
    if ci[1] < 0:
        return "better"
    if ci[0] > 0:
        return "worse"
    return "inconclusive"


def _cluster_ids(folds: Sequence[Fold], questions: dict[str, str]) -> list[int]:
    """Markets whose questions are nearly the same are one cluster."""
    return cluster_texts([questions[f.market_id] for f in folds])


# --------------------------------------------------------------------------- evaluation


def _evaluate_horizon(
    horizon: int,
    observations: Sequence[HistoricalObservation],
    exclusions: dict[str, int],
    cfg: PredictionConfig,
    min_calibration_n: int,
) -> HorizonReport:
    ordered = sorted(observations, key=lambda o: (o.decision_at, o.market_id))
    folds: list[Fold] = []
    for o in ordered:
        train = [t for t in ordered if t.resolution_at < o.decision_at]
        base = BaseRateShrinkage.fit(["YES" if t.outcome else "NO" for t in train], cfg)
        baseline = base.predict(
            FeatureVector(o.market_id, o.decision_at, {"market_probability": o.market_probability})
        ).probability
        calibrator = PlattCalibrator.fit(
            [t.market_probability for t in train],
            [t.outcome for t in train],
            min_n=min_calibration_n,
        )
        folds.append(
            Fold(
                market_id=o.market_id,
                horizon_days=horizon,
                decision_at=o.decision_at,
                resolution_at=o.resolution_at,
                n_train=len(train),
                market=o.market_probability,
                baseline=baseline,
                calibrated=None if calibrator is None else calibrator.predict(o.market_probability),
                outcome=o.outcome,
            )
        )

    common = [f for f in folds if f.calibrated is not None]
    outcomes = [f.outcome for f in common]
    series = {
        MARKET: [f.market for f in common],
        BASELINE: [f.baseline for f in common],
        CALIBRATED: [f.calibrated for f in common if f.calibrated is not None],
    }
    scores = [
        ModelScore(
            model=name,
            n=len(common),
            brier=brier_score(probs, outcomes),
            log_loss=log_loss(probs, outcomes),
            calibration=calibration_bins(probs, outcomes),
        )
        for name, probs in series.items()
    ]

    comparisons: list[PairedComparison] = []
    if common:
        questions = {o.market_id: o.question for o in ordered}
        clusters = _cluster_ids(common, questions)
        market_sq = [
            (p - (1.0 if o else 0.0)) ** 2 for p, o in zip(series[MARKET], outcomes, strict=True)
        ]
        for name in (BASELINE, CALIBRATED):
            sq = [
                (p - (1.0 if o else 0.0)) ** 2 for p, o in zip(series[name], outcomes, strict=True)
            ]
            diffs = [m - k for m, k in zip(sq, market_sq, strict=True)]
            ci_cluster, n_clusters = _cluster_ci(diffs, clusters)
            comparisons.append(
                PairedComparison(
                    model=name,
                    n=len(diffs),
                    n_clusters=n_clusters,
                    mean_brier_difference=sum(diffs) / len(diffs),
                    ci95_naive=_naive_ci(diffs),
                    ci95_clustered=ci_cluster,
                    verdict=_verdict(ci_cluster, n_clusters),
                )
            )

    notes = []
    if len(common) < len(folds):
        notes.append(
            f"{len(folds) - len(common)} early markets had fewer than {min_calibration_n} resolved "
            "markets to learn from: the calibrated price abstained, so they are in no comparison"
        )
    return HorizonReport(
        horizon_days=horizon,
        n_observations=len(folds),
        n_common=len(common),
        exclusions=exclusions,
        folds=folds,
        scores=scores,
        comparisons=comparisons,
        notes=notes,
    )


def evaluate_historical(
    observations: Sequence[HistoricalObservation],
    exclusions: dict[int, dict[str, int]],
    cfg: PredictionConfig,
    *,
    min_calibration_n: int = DEFAULT_MIN_N,
) -> HistoricalReport:
    by_horizon: dict[int, list[HistoricalObservation]] = {h: [] for h in exclusions}
    for o in observations:
        by_horizon.setdefault(o.horizon_days, []).append(o)
    horizons = [
        _evaluate_horizon(h, by_horizon[h], exclusions.get(h, {}), cfg, min_calibration_n)
        for h in sorted(by_horizon)
    ]
    return HistoricalReport(
        horizons=horizons,
        context_model={
            "status": "NOT_EVALUATED",
            "reason": (
                "no point-in-time contextual feature exists for markets that resolved before the "
                "feature journal began; a source response fetched today is not what was known "
                "then, and no historical observation is manufactured"
            ),
        },
        method={
            "decision_point": "market age (horizon_days after creation), market still open",
            "price": "probAfter of the last bet at or before the decision instant",
            "training": "markets of the same horizon resolved strictly before the decision instant",
            "interval": (
                "mean paired squared-error difference +/- 1.96 standard errors; naive = sample "
                "standard deviation (n-1) over markets, clustered = CR1 over question clusters"
            ),
            "verdict": f"only with at least {MIN_CLUSTERS_FOR_VERDICT} clusters and an interval "
            "that excludes zero",
        },
    )

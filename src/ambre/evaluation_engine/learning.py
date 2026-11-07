"""Chronological learning protocol on the bet-level history (pure, no I/O; ADR-022).

Everything fixed by ADR-022 lives in ``LearningConfig``. Models are fitted on the training side
only; the validation outcomes are read to score, never to fit or to choose. A market is on one
side at most, and a training market whose question belongs to a cluster with a validation market
is removed: near-duplicate questions are the same event.

A research report, not a licence to bet: ``candidates_for_shadow`` allows a shadow run only.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ambre.config.settings import PredictionConfig
from ambre.domain.similarity import cluster_texts
from ambre.evaluation_engine.historical import (
    MIN_CLUSTERS_FOR_VERDICT,
    _verdict,
    cluster_interval,
    family_z,
)
from ambre.evaluation_engine.history import BetLevelMarket
from ambre.evaluation_engine.metrics import (
    LOG_LOSS_EPS,
    CalibrationBin,
    brier_score,
    calibration_bins,
    log_loss,
)
from ambre.feature_engine.features import FeatureVector
from ambre.feature_engine.historical_features import (
    ACTIVITY_FEATURES,
    PATH_FEATURES,
    Features,
    features_at,
)
from ambre.prediction_engine.baselines import BaseRateShrinkage
from ambre.prediction_engine.logistic import AnchoredLogistic

MARKET = "market_price"
BASELINE = "ambre_baseline"
RECALIBRATED = "recalibrated"
PRICE_PATH = "price_path"
FULL = "full"
L2_GRID = (1.0, 10.0, 100.0, 1000.0)
INNER_VALIDATION_SHARE = 0.25
MODEL_FEATURES: dict[str, tuple[str, ...]] = {
    RECALIBRATED: (),
    PRICE_PATH: PATH_FEATURES,
    FULL: PATH_FEATURES + ACTIVITY_FEATURES,
}
#: at every horizon, the four models against the price, then the two feature models against
#: the recalibrated price (ADR-022)
COMPARISONS: tuple[tuple[str, str], ...] = (
    (BASELINE, MARKET),
    (RECALIBRATED, MARKET),
    (PRICE_PATH, MARKET),
    (FULL, MARKET),
    (PRICE_PATH, RECALIBRATED),
    (FULL, RECALIBRATED),
)


@dataclass(frozen=True)
class LearningConfig:
    split_at: datetime = datetime(2025, 7, 1, tzinfo=UTC)
    validation_end: datetime = datetime(2026, 5, 1, tzinfo=UTC)
    horizons_days: tuple[int, ...] = (1, 3, 7, 30)

    def __post_init__(self) -> None:
        if self.split_at.tzinfo is None or self.validation_end.tzinfo is None:
            raise ValueError("the cutoffs must be timezone-aware")
        if self.split_at >= self.validation_end:
            raise ValueError("the validation must end after the cutoff")


@dataclass(frozen=True)
class Example:
    market_id: str
    question: str
    horizon_days: int
    decision_at: datetime
    market_probability: float
    features: Features
    outcome: bool


@dataclass(frozen=True)
class Split:
    train_ids: set[str]
    validation_ids: set[str]
    purged_ids: set[str]
    unused: dict[str, int]


@dataclass(frozen=True)
class Score:
    model: str
    n: int
    brier: float | None
    log_loss: float | None
    #: mean absolute gap between forecast and observed rate, weighted by bin size
    calibration_error: float | None
    calibration: list[CalibrationBin]


@dataclass(frozen=True)
class Comparison:
    """``model`` minus ``reference``: a negative difference means the model is better."""

    model: str
    reference: str
    n: int
    n_clusters: int
    mean_brier_difference: float | None
    ci95_clustered: tuple[float, float] | None
    ci_family: tuple[float, float] | None
    mean_log_loss_difference: float | None
    log_loss_ci_family: tuple[float, float] | None
    #: mean Brier difference in each half of the validation, oldest first
    by_half: tuple[float | None, float | None]
    n_comparisons: int
    verdict: str  # better | worse | inconclusive


@dataclass(frozen=True)
class FittedModel:
    name: str
    l2: float
    a: float
    b: float
    weights: dict[str, float]
    missing_rate: dict[str, float]
    n: int


@dataclass(frozen=True)
class HorizonLearning:
    horizon_days: int
    n_train: int
    n_validation: int
    exclusions: dict[str, int]
    models: list[FittedModel]
    scores: list[Score]
    comparisons: list[Comparison]
    candidates_for_shadow: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LearningReport:
    train_markets: int
    validation_markets: int
    purged_markets: int
    unused: dict[str, int]
    n_comparisons: int
    horizons: list[HorizonLearning]
    method: dict[str, str]


def build_examples(
    markets: Sequence[BetLevelMarket], horizons_days: Sequence[int]
) -> tuple[list[Example], dict[int, dict[str, int]]]:
    """One example per market and horizon, and why the others do not exist."""
    examples: list[Example] = []
    excluded: dict[int, dict[str, int]] = {h: {} for h in horizons_days}

    def skip(horizon: int, reason: str) -> None:
        excluded[horizon][reason] = excluded[horizon].get(reason, 0) + 1

    for m in markets:
        for horizon in horizons_days:
            at = m.created_at + timedelta(days=horizon)
            if not m.complete:
                skip(horizon, "incomplete_read")
            elif not m.events:
                skip(horizon, "no_bet")
            elif at >= m.resolution_at:
                skip(horizon, "resolved_before_decision")
            elif m.close_at is not None and at >= m.close_at:
                skip(horizon, "closed_before_decision")
            else:
                features = features_at(m.created_at, m.events, at)
                price = features["price"]
                assert price is not None
                examples.append(
                    Example(m.market_id, m.question, horizon, at, price, features, m.outcome)
                )
    return examples, {h: dict(sorted(r.items())) for h, r in excluded.items()}


def chronological_split(markets: Sequence[BetLevelMarket], cfg: LearningConfig) -> Split:
    train: set[str] = set()
    validation: set[str] = set()
    unused: dict[str, int] = {}

    def skip(reason: str) -> None:
        unused[reason] = unused.get(reason, 0) + 1

    for m in markets:
        if m.created_at >= cfg.validation_end:
            skip("after_validation")
        elif m.created_at >= cfg.split_at:
            validation.add(m.market_id)
        elif m.resolution_at < cfg.split_at:
            train.add(m.market_id)
        else:
            skip("straddles_cutoff")
    kept = [m for m in markets if m.market_id in train | validation]
    clusters = cluster_texts([m.question for m in kept])
    validation_clusters = {
        c for m, c in zip(kept, clusters, strict=True) if m.market_id in validation
    }
    purged = {
        m.market_id
        for m, c in zip(kept, clusters, strict=True)
        if m.market_id in train and c in validation_clusters
    }
    return Split(train - purged, validation, purged, dict(sorted(unused.items())))


def _fit(examples: Sequence[Example], names: Sequence[str], l2: float) -> AnchoredLogistic | None:
    return AnchoredLogistic.fit(
        [e.features for e in examples],
        [e.market_probability for e in examples],
        [e.outcome for e in examples],
        names,
        l2=l2,
    )


def _choose_l2(train: Sequence[Example], names: Sequence[str]) -> float:
    """The penalty with the best log loss on the last quarter of the training side."""
    ordered = sorted(train, key=lambda e: (e.decision_at, e.market_id))
    cut = int(len(ordered) * (1.0 - INNER_VALIDATION_SHARE))
    inner, held = ordered[:cut], ordered[cut:]
    best, best_loss = L2_GRID[-1], float("inf")
    for l2 in L2_GRID:
        model = _fit(inner, names, l2)
        if model is None or not held:
            continue
        loss = log_loss(
            [model.predict(e.features, e.market_probability) for e in held],
            [e.outcome for e in held],
        )
        if loss is not None and loss < best_loss:
            best, best_loss = l2, loss
    return best


def _squared(p: float, outcome: bool) -> float:
    return (p - (1.0 if outcome else 0.0)) ** 2


def _log_error(p: float, outcome: bool) -> float:
    return log_loss([p], [outcome]) or LOG_LOSS_EPS


def _calibration_error(bins: Sequence[CalibrationBin]) -> float | None:
    total = sum(b.n for b in bins)
    if not total:
        return None
    return sum(b.n * abs(b.mean_forecast - b.observed_rate) for b in bins) / total


def _halves(
    diffs: Sequence[float], instants: Sequence[datetime]
) -> tuple[float | None, float | None]:
    ordered = [d for _, d in sorted(zip(instants, diffs, strict=True), key=lambda x: x[0])]
    middle = len(ordered) // 2
    first, second = ordered[:middle], ordered[middle:]
    return (
        sum(first) / len(first) if first else None,
        sum(second) / len(second) if second else None,
    )


def _compare(
    model: str,
    reference: str,
    forecasts: dict[str, list[float]],
    examples: Sequence[Example],
    clusters: Sequence[int],
    family: int,
) -> Comparison:
    outcomes = [e.outcome for e in examples]
    brier = [
        _squared(p, o) - _squared(r, o)
        for p, r, o in zip(forecasts[model], forecasts[reference], outcomes, strict=True)
    ]
    logs = [
        _log_error(p, o) - _log_error(r, o)
        for p, r, o in zip(forecasts[model], forecasts[reference], outcomes, strict=True)
    ]
    z = family_z(family)
    ci, n_clusters = cluster_interval(brier, clusters)
    ci_family, _ = cluster_interval(brier, clusters, z)
    log_family, _ = cluster_interval(logs, clusters, z)
    n = len(brier)
    return Comparison(
        model=model,
        reference=reference,
        n=n,
        n_clusters=n_clusters,
        mean_brier_difference=sum(brier) / n if n else None,
        ci95_clustered=ci,
        ci_family=ci_family,
        mean_log_loss_difference=sum(logs) / n if n else None,
        log_loss_ci_family=log_family,
        by_half=_halves(brier, [e.decision_at for e in examples]),
        n_comparisons=family,
        verdict=_verdict(ci_family, n_clusters),
    )


def _candidates(comparisons: Sequence[Comparison]) -> list[str]:
    """ADR-022: beat the price and the recalibrated price, no worse log loss, stable sign."""
    out = []
    table = {(c.model, c.reference): c for c in comparisons}
    for name in (PRICE_PATH, FULL):
        market, recalibrated = table[(name, MARKET)], table[(name, RECALIBRATED)]
        halves = market.by_half
        stable = all(h is not None and h < 0 for h in halves)
        log_ci = market.log_loss_ci_family
        no_worse = log_ci is not None and log_ci[0] <= 0
        if market.verdict == "better" and recalibrated.verdict == "better" and stable and no_worse:
            out.append(name)
    return out


def _horizon(
    horizon: int,
    train: Sequence[Example],
    validation: Sequence[Example],
    exclusions: dict[str, int],
    prediction: PredictionConfig,
    family: int,
) -> HorizonLearning:
    fitted: dict[str, AnchoredLogistic] = {}
    for name, names in MODEL_FEATURES.items():
        if len(train) < 2:
            continue
        model = _fit(train, names, _choose_l2(train, names))
        if model is not None:
            fitted[name] = model
    if len(fitted) < len(MODEL_FEATURES) or not validation:
        return HorizonLearning(horizon, len(train), len(validation), exclusions, [], [], [], [])
    base = BaseRateShrinkage.fit(["YES" if e.outcome else "NO" for e in train], prediction)
    forecasts: dict[str, list[float]] = {
        MARKET: [e.market_probability for e in validation],
        BASELINE: [
            base.predict(
                FeatureVector(
                    e.market_id, e.decision_at, {"market_probability": e.market_probability}
                )
            ).probability
            for e in validation
        ],
    }
    for name, model in fitted.items():
        forecasts[name] = [model.predict(e.features, e.market_probability) for e in validation]
    outcomes = [e.outcome for e in validation]
    scores = []
    for name, probs in forecasts.items():
        bins = calibration_bins(probs, outcomes)
        scores.append(
            Score(
                name,
                len(probs),
                brier_score(probs, outcomes),
                log_loss(probs, outcomes),
                _calibration_error(bins),
                bins,
            )
        )
    clusters = cluster_texts([e.question for e in validation])
    comparisons = [
        _compare(model, reference, forecasts, validation, clusters, family)
        for model, reference in COMPARISONS
    ]
    return HorizonLearning(
        horizon_days=horizon,
        n_train=len(train),
        n_validation=len(validation),
        exclusions=exclusions,
        models=[
            FittedModel(
                name,
                m.l2,
                m.a,
                m.b,
                dict(zip(m.names, m.weights, strict=True)),
                m.missing_rate,
                m.n,
            )
            for name, m in fitted.items()
        ],
        scores=scores,
        comparisons=comparisons,
        candidates_for_shadow=_candidates(comparisons),
    )


def run_learning(
    markets: Sequence[BetLevelMarket], cfg: LearningConfig, prediction: PredictionConfig
) -> LearningReport:
    split = chronological_split(markets, cfg)
    examples, excluded = build_examples(
        [m for m in markets if m.market_id in split.train_ids | split.validation_ids],
        cfg.horizons_days,
    )
    by_horizon: dict[int, list[Example]] = defaultdict(list)
    for e in examples:
        by_horizon[e.horizon_days].append(e)
    family = len(cfg.horizons_days) * len(COMPARISONS)
    horizons = []
    for horizon in cfg.horizons_days:
        rows = by_horizon[horizon]
        horizons.append(
            _horizon(
                horizon,
                [e for e in rows if e.market_id in split.train_ids],
                [e for e in rows if e.market_id in split.validation_ids],
                excluded[horizon],
                prediction,
                family,
            )
        )
    return LearningReport(
        train_markets=len(split.train_ids),
        validation_markets=len(split.validation_ids),
        purged_markets=len(split.purged_ids),
        unused=split.unused,
        n_comparisons=family,
        horizons=horizons,
        method={
            "protocol": "ADR-022, fixed before any result on the corrected data",
            "split": f"cutoff {cfg.split_at.isoformat()}, validation to "
            f"{cfg.validation_end.isoformat()}, near-duplicate questions purged from training",
            "interval": "paired difference, CR1 over question clusters, Bonferroni over the "
            f"{family} comparisons",
            "verdict": f"at least {MIN_CLUSTERS_FOR_VERDICT} clusters and a family interval "
            "that excludes zero",
            "limits": "validation already read by ADR-018 with faulty prices; resolved markets "
            "only; no return is measured; a shadow run is the only true out-of-sample test",
        },
    )

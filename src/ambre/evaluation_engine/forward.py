"""The pre-registered criterion of the forward test (ADR-019), applied mechanically (pure).

One comparison, fixed in advance: on the first decision of each market taken inside the age
window, the Brier score of the model against the Brier score of the market price, with the
cluster-robust 95% interval of the paired difference (``z = 1.96``: a single comparison, so no
family correction). The verdict is withheld, and so is every figure that could hint at it, until
there are at least ``MIN_MARKETS`` resolved markets in at least ``MIN_CLUSTERS`` clusters of
different questions: the rule against stopping when the interval happens to look good is enforced
here, not left to discipline.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ambre.domain.similarity import cluster_texts
from ambre.evaluation_engine.historical import cluster_interval

MIN_MARKETS = 300
MIN_CLUSTERS = 30
Z95 = 1.96


@dataclass(frozen=True)
class ForwardObservation:
    market_id: str
    question: str
    market_probability: float
    estimated_probability: float
    outcome: bool


@dataclass(frozen=True)
class ForwardReport:
    status: str  # INSUFFICIENT_SAMPLE | CONFIRMED | REFUTED | NOT_CONFIRMED
    reason: str
    n: int
    n_clusters: int
    comparisons: int = 1
    z: float = Z95
    brier_market: float | None = None
    brier_model: float | None = None
    mean_brier_difference: float | None = None
    ci95_clustered: tuple[float, float] | None = None


def evaluate_forward(observations: Sequence[ForwardObservation]) -> ForwardReport:
    n = len(observations)
    clusters = cluster_texts([o.question for o in observations])
    n_clusters = len(set(clusters))
    if n < MIN_MARKETS or n_clusters < MIN_CLUSTERS:
        return ForwardReport(
            status="INSUFFICIENT_SAMPLE",
            reason=(
                f"{n} resolved markets in {n_clusters} clusters: the verdict and every figure "
                f"behind it stay hidden until {MIN_MARKETS} markets and {MIN_CLUSTERS} clusters"
            ),
            n=n,
            n_clusters=n_clusters,
        )
    market_sq = [(o.market_probability - float(o.outcome)) ** 2 for o in observations]
    model_sq = [(o.estimated_probability - float(o.outcome)) ** 2 for o in observations]
    diffs = [m - k for m, k in zip(model_sq, market_sq, strict=True)]
    ci, _ = cluster_interval(diffs, clusters, Z95)
    mean = sum(diffs) / n
    if ci is None:
        status, reason = "NOT_CONFIRMED", "the interval could not be computed"
    elif ci[1] < 0:
        status, reason = "CONFIRMED", "the model's Brier is lower and the interval excludes zero"
    elif ci[0] > 0:
        status, reason = "REFUTED", "the model's Brier is higher and the interval excludes zero"
    else:
        status, reason = "NOT_CONFIRMED", "the interval includes zero: the bias is not confirmed"
    return ForwardReport(
        status=status,
        reason=reason,
        n=n,
        n_clusters=n_clusters,
        brier_market=sum(market_sq) / n,
        brier_model=sum(model_sq) / n,
        mean_brier_difference=mean,
        ci95_clustered=ci,
    )

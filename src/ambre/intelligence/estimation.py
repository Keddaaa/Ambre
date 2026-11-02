"""Estimation gate (pure): a probability only from a validated, applicable model.

There is no built-in model: none has been trained and validated on resolved markets. Until one
exists the answer is ``INSUFFICIENT_MODEL``; a probability is never produced from a news item, a
forecast or a coefficient picked by hand. An ambiguous market is ``NOT_ESTIMABLE`` whatever the
model.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ambre.intelligence.context import FeatureSet
from ambre.intelligence.generic_planning import MarketPlan


class EstimationStatus(StrEnum):
    ESTIMATED = "ESTIMATED"
    INSUFFICIENT_MODEL = "INSUFFICIENT_MODEL"
    NOT_ESTIMABLE = "NOT_ESTIMABLE"


class ProbabilityModel(Protocol):
    version: str
    #: True only after a held-out validation (the experiment engine, a later phase).
    validated: bool

    def applies_to(self, plan: MarketPlan) -> bool: ...

    def predict(self, features: dict[str, float | None]) -> float: ...


@dataclass(frozen=True)
class EstimationResult:
    status: EstimationStatus
    probability: float | None
    model_version: str | None
    reasons: tuple[str, ...]
    blocking: tuple[str, ...]
    feature_names: tuple[str, ...]


def estimate_from_context(
    plan: MarketPlan, features: FeatureSet, models: Sequence[ProbabilityModel]
) -> EstimationResult:
    names = tuple(f.name for f in features.features)

    def result(
        status: EstimationStatus,
        reasons: list[str],
        probability: float | None = None,
        version: str | None = None,
    ) -> EstimationResult:
        return EstimationResult(status, probability, version, tuple(reasons), plan.blocking, names)

    candidates = [m for m in models if m.validated and m.applies_to(plan)]
    if not plan.estimable:
        reasons = ["the market is not estimable: the question leaves something open"]
        if not candidates:
            reasons.append(f"no validated model for domain {plan.domain.value} either")
        return result(EstimationStatus.NOT_ESTIMABLE, reasons)
    if not candidates:
        return result(
            EstimationStatus.INSUFFICIENT_MODEL,
            [f"no validated model for domain {plan.domain.value}"],
        )
    if not features.features:
        return result(
            EstimationStatus.INSUFFICIENT_MODEL, ["no context features are available to the model"]
        )
    model = candidates[0]
    probability = model.predict({f.name: f.value for f in features.features})
    if not (math.isfinite(probability) and 0.0 < probability < 1.0):
        return result(
            EstimationStatus.INSUFFICIENT_MODEL,
            [f"model {model.version} returned an invalid probability: {probability!r}"],
        )
    return result(EstimationStatus.ESTIMATED, [], probability, model.version)


#: Validated models available to the pipeline. Empty on purpose: none has been trained and
#: validated on resolved markets yet (the experiment engine is a later phase).
REGISTERED_MODELS: list[ProbabilityModel] = []


def model_available(plan: MarketPlan) -> bool:
    return any(m.validated and m.applies_to(plan) for m in REGISTERED_MODELS)

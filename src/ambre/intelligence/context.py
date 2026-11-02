"""Context Feature Bridge (pure): from stored observations to dated features.

Only information that is (1) from an enabled, verified provider, (2) valid for its kind, and (3)
already available at the decision time can become a feature. A forecast is a model output and is
kept as a forecast; an earthquake counts only when the source has reviewed it. A feature is never
a probability and never a coefficient: it is an input for a model that has been trained and
validated elsewhere. Nothing here modifies feature_engine; the bridge only adds prefixed values
to a copy of its vector.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ambre.feature_engine.features import FeatureVector
from ambre.intelligence.generic_planning import MarketPlan
from ambre.intelligence.matching import MAX_PLACE_DISTANCE_DEG, match_observation
from ambre.intelligence.models import (
    EarthquakePayload,
    InfoNeed,
    Observation,
    ObservationKind,
    ProviderStatus,
    VerificationStatus,
    WeatherForecastPayload,
    is_actionable,
)
from ambre.intelligence.registry import Registry

_VERIFIED = (ProviderStatus.VERIFIED_FREE, ProviderStatus.VERIFIED_LIMITED)
_BAD_STATUS = (
    VerificationStatus.RETRACTED,
    VerificationStatus.CONTRADICTED,
    VerificationStatus.OUTDATED,
)


@dataclass(frozen=True)
class ContextFeature:
    name: str
    value: float | None
    unit: str | None
    kind: str
    provider_id: str
    available_at: datetime
    observation_id: str | None = None


@dataclass(frozen=True)
class FeatureSet:
    market_id: str
    features: tuple[ContextFeature, ...] = ()
    #: (observation id, reason) for observations that matched the market but were not usable
    rejected: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def eligibility_reasons(obs: Observation, registry: Registry, now: datetime) -> list[str]:
    reasons: list[str] = []
    card = registry.get(obs.provider_id)
    if card is None:
        reasons.append(f"provider {obs.provider_id} is not in the registry")
    elif not card.enabled:
        reasons.append(f"provider {obs.provider_id} is not enabled")
    elif card.status not in _VERIFIED:
        reasons.append(f"provider {obs.provider_id} has status {card.status.value}")
    if obs.verification_status in _BAD_STATUS:
        reasons.append(f"observation is {obs.verification_status.value}")
    if obs.available_at is None:
        reasons.append("availability unknown: it cannot be placed before the decision")
    elif obs.available_at > now:
        reasons.append("available after the decision time")
    if obs.kind is ObservationKind.OBSERVATION and not is_actionable(obs):
        reasons.append("not verified by the source")
    return reasons


def _near(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> bool:
    return (
        abs(lat_a - lat_b) <= MAX_PLACE_DISTANCE_DEG
        and abs(lon_a - lon_b) <= MAX_PLACE_DISTANCE_DEG
    )


def _weather_features(
    plan: MarketPlan, observations: Sequence[Observation], registry: Registry, now: datetime
) -> FeatureSet:
    params = plan.weather
    assert params is not None
    rejected: list[tuple[str, str]] = []
    usable: list[Observation] = []
    for obs in observations:
        p = obs.payload
        if not (
            isinstance(p, WeatherForecastPayload)
            and p.variable == params.variable
            and p.target_date == params.target_date.isoformat()
            and _near(p.latitude, p.longitude, params.latitude, params.longitude)
        ):
            continue  # about another day, variable or place: not this market's business
        reasons = eligibility_reasons(obs, registry, now)
        if p.value is None:
            reasons.append("forecast has no value")
        if reasons:
            rejected.append((obs.observation_id, "; ".join(reasons)))
        else:
            usable.append(obs)
    if not usable:
        return FeatureSet(plan.market_id, (), tuple(rejected))
    latest = max(usable, key=lambda o: (o.available_at or o.ingested_at, o.ingested_at))
    payload = latest.payload
    assert isinstance(payload, WeatherForecastPayload) and latest.available_at is not None
    assert payload.value is not None
    base = f"weather.forecast.{params.variable}"

    def feature(name: str, value: float, unit: str | None) -> ContextFeature:
        return ContextFeature(
            name,
            value,
            unit,
            "forecast",
            latest.provider_id,
            latest.available_at,  # type: ignore[arg-type]
            latest.observation_id,
        )

    out = [
        feature(base, payload.value, payload.unit),
        feature("weather.forecast.age_seconds", (now - latest.available_at).total_seconds(), "s"),
        feature(
            "weather.forecast.horizon_days",
            float((params.target_date - latest.available_at.date()).days),
            "d",
        ),
    ]
    if (
        params.threshold_value is not None
        and params.threshold_unit is not None
        and payload.unit == params.threshold_unit
        and math.isfinite(payload.value)
    ):
        out.append(feature(f"{base}.margin", payload.value - params.threshold_value, payload.unit))
    return FeatureSet(plan.market_id, tuple(out), tuple(rejected))


def _earthquake_features(
    plan: MarketPlan,
    observations: Sequence[Observation],
    registry: Registry,
    now: datetime,
    market_created_at: datetime,
    collected_at: datetime | None,
) -> FeatureSet:
    gap = plan.gap
    assert gap is not None
    if collected_at is None:
        return FeatureSet(plan.market_id)  # nothing was searched: a zero count would be invented
    rejected: list[tuple[str, str]] = []
    usable: list[Observation] = []
    for obs in observations:
        if not isinstance(obs.payload, EarthquakePayload):
            continue
        if match_observation(obs, gap, market_created_at=market_created_at, now=now) is None:
            continue
        reasons = eligibility_reasons(obs, registry, now)
        if reasons:
            rejected.append((obs.observation_id, "; ".join(reasons)))
        else:
            usable.append(obs)
    magnitudes = [
        o.payload.magnitude
        for o in usable
        if isinstance(o.payload, EarthquakePayload) and o.payload.magnitude is not None
    ]
    kwargs = {
        "provider_id": "usgs_earthquakes",
        "available_at": collected_at,
        "kind": "observation",
    }
    out = [
        ContextFeature("quake.matching_event_count", float(len(usable)), "events", **kwargs),  # type: ignore[arg-type]
        ContextFeature(
            "quake.max_matching_magnitude",
            max(magnitudes) if magnitudes else None,
            "magnitude",
            **kwargs,  # type: ignore[arg-type]
        ),
    ]
    return FeatureSet(plan.market_id, tuple(out), tuple(rejected))


def build_feature_set(
    plan: MarketPlan,
    observations: Sequence[Observation],
    registry: Registry,
    now: datetime,
    *,
    market_created_at: datetime,
    collected_at: datetime | None = None,
) -> FeatureSet:
    if plan.need is InfoNeed.WEATHER_FORECAST and plan.weather is not None:
        return _weather_features(plan, observations, registry, now)
    if plan.need is InfoNeed.RECENT_EARTHQUAKES and plan.gap is not None:
        return _earthquake_features(
            plan, observations, registry, now, market_created_at, collected_at
        )
    return FeatureSet(plan.market_id)


def augment_vector(vector: FeatureVector, features: FeatureSet, now: datetime) -> FeatureVector:
    """A copy of ``vector`` with the context features added under a ``ctx.`` prefix.

    The result is only as old as its newest component, so ``available_at`` can only move forward.
    """
    values = dict(vector.values)
    available = vector.available_at
    for f in features.features:
        if f.available_at > now:
            raise ValueError(f"feature {f.name} is available after {now.isoformat()}: future data")
        values[f"ctx.{f.name}"] = f.value
        available = max(available, f.available_at)
    return FeatureVector(market_id=vector.market_id, available_at=available, values=values)

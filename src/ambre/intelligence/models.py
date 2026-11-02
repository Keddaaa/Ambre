"""Typed information envelope, provider cards and fetch results (pure, no I/O).

Four clocks are kept apart and never merged: ``event_at`` (when it happened or what the
value refers to), ``published_at`` (when the source published it), ``available_at`` (since
when this version could be read at the source) and ``ingested_at`` (when AMBRE fetched
it). Any of the first three may be unknown and stays ``None``.
"""

import math
import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

_NAMESPACE = uuid.UUID("6f1d2c1e-3b57-4a54-9a43-0a6b1c0f9a11")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderStatus(StrEnum):
    VERIFIED_FREE = "VERIFIED_FREE"
    VERIFIED_LIMITED = "VERIFIED_LIMITED"
    REQUIRES_REGISTRATION = "REQUIRES_REGISTRATION"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    PAID_ONLY = "PAID_ONLY"
    UNAVAILABLE = "UNAVAILABLE"
    UNVERIFIED = "UNVERIFIED"


class Domain(StrEnum):
    SEISMIC = "seismic"
    WEATHER = "weather"
    NATURAL_EVENTS = "natural_events"
    NEWS = "news"
    TECHNOLOGY = "technology"
    SOCIAL = "social"
    MARKETS = "markets"


class InfoNeed(StrEnum):
    """What a market lacks. A provider declares which needs it can serve."""

    RECENT_EARTHQUAKES = "recent_earthquakes"
    WEATHER_FORECAST = "weather_forecast"


class ObservationKind(StrEnum):
    OBSERVATION = "observation"  # something that was measured or reported
    FORECAST = "forecast"  # a model output: never an observation


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    CONFIRMED = "confirmed"
    CORROBORATED = "corroborated"
    CONTRADICTED = "contradicted"
    OUTDATED = "outdated"
    RETRACTED = "retracted"


class ResultKind(StrEnum):
    OK = "ok"
    EMPTY = "empty"  # valid answer, no data
    INCONCLUSIVE = "inconclusive"  # the answer proves neither data nor absence of data
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class RateLimits(_Frozen):
    """Request caps over rolling windows. ``None`` means no such cap is known or set."""

    per_minute: Annotated[int, Field(gt=0)] | None = None
    per_hour: Annotated[int, Field(gt=0)] | None = None
    per_day: Annotated[int, Field(gt=0)] | None = None
    min_interval_seconds: Annotated[float, Field(gt=0)] | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.per_minute is None
            and self.per_hour is None
            and self.per_day is None
            and self.min_interval_seconds is None
        )


class ProviderCard(_Frozen):
    """Registry entry. ``internal_budget`` is what AMBRE enforces; ``provider_limits`` is
    what the provider documents (informational, may be empty = unknown)."""

    id: str = Field(min_length=1)
    name: str
    domain: Domain
    status: ProviderStatus = ProviderStatus.UNVERIFIED
    enabled: bool = False
    authentication: Literal["none", "api_key"] = "none"
    provider_limits: RateLimits = Field(default_factory=RateLimits)
    internal_budget: RateLimits = Field(default_factory=RateLimits)
    reserve_fraction: Annotated[float, Field(ge=0, lt=1)] = 0.2
    allow_commercial_use: bool | None = None
    verified_on: str | None = None
    freshness_seconds: Annotated[int, Field(gt=0)] | None = None
    # info need -> a priori relevance in [0, 1]; a prior, not a measurement
    capabilities: dict[InfoNeed, Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=dict)

    def provides(self, need: InfoNeed) -> bool:
        return need in self.capabilities


class EarthquakePayload(_Frozen):
    payload_type: Literal["earthquake"] = "earthquake"
    native_id: str
    magnitude: float | None = None
    place: str | None = None
    latitude: Annotated[float, Field(ge=-90, le=90)] | None = None
    longitude: Annotated[float, Field(ge=-180, le=180)] | None = None
    depth_km: float | None = None
    review_status: Literal["automatic", "reviewed"] | None = None
    tsunami: bool | None = None

    @field_validator("magnitude", "depth_km")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("value must be finite")
        return v


class WeatherForecastPayload(_Frozen):
    """One forecast value for one place and one calendar day. A forecast is a model output:
    it is never an observation of what happened."""

    payload_type: Literal["weather_forecast"] = "weather_forecast"
    variable: Literal["temperature_max", "temperature_min", "precipitation_sum"]
    target_date: str  # ISO date in the place's own timezone
    value: float | None = None  # None when the provider returned no value
    unit: str | None = None
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    timezone: str | None = None

    @field_validator("value")
    @classmethod
    def _finite_value(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("value must be finite")
        return v


Payload = Annotated[EarthquakePayload | WeatherForecastPayload, Field(discriminator="payload_type")]


class Observation(_Frozen):
    observation_id: str
    provider_id: str
    domain: Domain
    kind: ObservationKind
    payload: Payload
    ingested_at: AwareDatetime
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    title: str | None = None
    source_url: str | None = None
    event_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    available_at: AwareDatetime | None = None
    source_reliability: Annotated[float, Field(ge=0, le=1)] | None = None
    revision: str | None = None


class FetchResult(_Frozen):
    kind: ResultKind
    observations: tuple[Observation, ...] = ()
    detail: str | None = None
    retry_after_seconds: float | None = None
    http_status: int | None = None
    invalid_items: int = 0


def make_observation_id(provider_id: str, native_id: str, revision: str | None) -> str:
    """Stable id: the same provider record at the same revision is the same observation."""
    return str(uuid.uuid5(_NAMESPACE, f"{provider_id}|{native_id}|{revision or ''}"))


def is_actionable(obs: Observation) -> bool:
    """Only a verified observation may inform a prediction; a forecast never does as a fact."""
    return obs.kind is ObservationKind.OBSERVATION and obs.verification_status in (
        VerificationStatus.CONFIRMED,
        VerificationStatus.CORROBORATED,
    )

"""Market matching: decide whether an observation is a candidate event for a market gap (pure).

A link records a relation with its provenance and evidence level. It is never a fact about
the market: the resolution criteria are not read yet, so ``usable_for_prediction`` is always
False in this version. Matching uses only what was available at ``now`` (no future data).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ambre.intelligence.domain_params import WeatherParams
from ambre.intelligence.models import (
    EarthquakePayload,
    Observation,
    VerificationStatus,
    WeatherForecastPayload,
)
from ambre.intelligence.planning import Gap

#: A forecast grid point is a few kilometres from the requested place (Open-Meteo snaps it).
MAX_PLACE_DISTANCE_DEG = 0.25


class CriteriaStatus(StrEnum):
    FORECAST_MATCHES_QUESTION = "forecast_matches_question"
    THRESHOLD_MET_CRITERIA_UNREAD = "threshold_met_criteria_unread"
    THRESHOLD_UNKNOWN = "threshold_unknown"


@dataclass(frozen=True)
class MarketLink:
    market_id: str
    observation_id: str
    relation: str
    criteria_status: CriteriaStatus
    evidence_level: str
    usable_for_prediction: bool
    reasons: tuple[str, ...]


def match_observation(
    obs: Observation, gap: Gap, *, market_created_at: datetime, now: datetime
) -> MarketLink | None:
    payload = obs.payload
    if not isinstance(payload, EarthquakePayload):
        return None
    if obs.verification_status is VerificationStatus.RETRACTED:
        return None
    if obs.event_at is None or obs.event_at < market_created_at or obs.event_at > now:
        return None  # cannot be placed inside the market's lifetime
    visible_since = obs.available_at or obs.ingested_at
    if visible_since > now:
        return None  # not yet readable at decision time
    if gap.region is None:
        return None  # no region to anchor the link: guessing would invent a relation
    in_region = gap.region.matches_text(payload.place) or gap.region.contains(
        payload.latitude, payload.longitude
    )
    if not in_region:
        return None

    reasons = [f"event in {gap.region.name}", f"event_at {obs.event_at.isoformat()}"]
    if gap.min_magnitude is None:
        status = CriteriaStatus.THRESHOLD_UNKNOWN
        reasons.append("magnitude threshold of the market unknown")
    else:
        if payload.magnitude is None or payload.magnitude < gap.min_magnitude:
            return None
        status = CriteriaStatus.THRESHOLD_MET_CRITERIA_UNREAD
        reasons.append(f"magnitude {payload.magnitude:g} >= {gap.min_magnitude:g}")
    reasons.append("resolution criteria not read: candidate only")
    return MarketLink(
        market_id=gap.market_id,
        observation_id=obs.observation_id,
        relation="candidate_event",
        criteria_status=status,
        evidence_level=obs.verification_status.value,
        usable_for_prediction=False,
        reasons=tuple(reasons),
    )


def match_forecast(
    obs: Observation, params: WeatherParams, market_id: str, now: datetime
) -> MarketLink | None:
    """Link a forecast to the market it forecasts: same variable, same calendar day, same place.

    The link says "this is the forecast for what the question asks"; it is an input for a model,
    never a statement that the market will resolve a given way.
    """
    p = obs.payload
    if not isinstance(p, WeatherForecastPayload):
        return None
    if p.variable != params.variable or p.target_date != params.target_date.isoformat():
        return None
    if (
        abs(p.latitude - params.latitude) > MAX_PLACE_DISTANCE_DEG
        or abs(p.longitude - params.longitude) > MAX_PLACE_DISTANCE_DEG
    ):
        return None
    if (obs.available_at or obs.ingested_at) > now:
        return None
    return MarketLink(
        market_id=market_id,
        observation_id=obs.observation_id,
        relation="forecast_input",
        criteria_status=CriteriaStatus.FORECAST_MATCHES_QUESTION,
        evidence_level=obs.verification_status.value,
        usable_for_prediction=False,
        reasons=(
            f"forecast of {p.variable} for {params.location_name} on {p.target_date}",
            "a forecast is a model output, not an observation of the outcome",
            "resolution criteria not read: input for a validated model only",
        ),
    )

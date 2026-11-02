"""Raw provider payloads -> typed observations (pure). Provenance is never dropped, absent
values stay ``None`` and invalid items are counted and skipped, never repaired."""

import math
from datetime import UTC, date, datetime
from typing import Any, get_args

from pydantic import ValidationError

from ambre.intelligence.models import (
    Domain,
    EarthquakePayload,
    FetchResult,
    Observation,
    ObservationKind,
    ResultKind,
    VerificationStatus,
    WeatherForecastPayload,
    make_observation_id,
)

USGS_ID = "usgs_earthquakes"
OPEN_METEO_ID = "open_meteo"
#: Our variable -> the Open-Meteo daily series (all three seen live on 2026-11-02).
OPEN_METEO_SERIES = {
    "temperature_max": "temperature_2m_max",
    "temperature_min": "temperature_2m_min",
    "precipitation_sum": "precipitation_sum",
}

_STATUS_TO_VERIFICATION = {
    "reviewed": VerificationStatus.CONFIRMED,  # reviewed by the primary source
    "automatic": VerificationStatus.UNVERIFIED,  # may still be revised
    "deleted": VerificationStatus.RETRACTED,
}


def _from_ms(value: Any) -> datetime | None:
    """Epoch milliseconds -> aware UTC, or None for anything missing or out of range."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, ValueError, OSError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)  # finiteness is enforced by the payload model


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_feature(feature: Any, ingested_at: datetime) -> Observation | None:
    if not isinstance(feature, dict):
        return None
    native_id = _str_or_none(feature.get("id"))
    props = feature.get("properties")
    if native_id is None or not isinstance(props, dict):
        return None
    event_at = _from_ms(props.get("time"))
    if event_at is None:
        return None
    updated_raw = props.get("updated")
    available_at = _from_ms(updated_raw)
    revision = (
        str(int(updated_raw))
        if available_at is not None and isinstance(updated_raw, int | float)
        else None
    )

    coords = (feature.get("geometry") or {}).get("coordinates") if feature.get("geometry") else None
    lon = lat = depth = None
    if isinstance(coords, list) and len(coords) >= 2:
        lon, lat = _number(coords[0]), _number(coords[1])
        depth = _number(coords[2]) if len(coords) >= 3 else None

    status = props.get("status")
    tsunami = props.get("tsunami")
    try:
        payload = EarthquakePayload(
            native_id=native_id,
            magnitude=_number(props.get("mag")),
            place=_str_or_none(props.get("place")),
            latitude=lat,
            longitude=lon,
            depth_km=depth,
            review_status=status if status in ("automatic", "reviewed") else None,
            tsunami=None if tsunami is None else bool(tsunami),
        )
        return Observation(
            observation_id=make_observation_id(USGS_ID, native_id, revision),
            provider_id=USGS_ID,
            domain=Domain.SEISMIC,
            kind=ObservationKind.OBSERVATION,
            payload=payload,
            ingested_at=ingested_at,
            verification_status=_STATUS_TO_VERIFICATION.get(
                status if isinstance(status, str) else "", VerificationStatus.UNVERIFIED
            ),
            title=_str_or_none(props.get("title")) or _str_or_none(props.get("place")),
            source_url=_str_or_none(props.get("url")),
            event_at=event_at,
            published_at=None,
            available_at=available_at,
            revision=revision,
        )
    except ValidationError:
        return None


def parse_usgs_geojson(body: object, ingested_at: datetime) -> FetchResult:
    """Parse a USGS FDSN/summary GeoJSON FeatureCollection."""
    if not isinstance(body, dict) or not isinstance(body.get("features"), list):
        return FetchResult(kind=ResultKind.INCONCLUSIVE, detail="unexpected response body")
    features = body["features"]
    if not features:
        return FetchResult(kind=ResultKind.EMPTY)
    parsed = [_parse_feature(f, ingested_at) for f in features]
    observations = tuple(o for o in parsed if o is not None)
    invalid = len(parsed) - len(observations)
    if not observations:
        return FetchResult(
            kind=ResultKind.INCONCLUSIVE, detail="every item was invalid", invalid_items=invalid
        )
    return FetchResult(kind=ResultKind.OK, observations=observations, invalid_items=invalid)


def _finite_or_none(value: Any) -> tuple[bool, float | None]:
    """(valid, value): a JSON null is a valid unknown, a non-number or non-finite value is not."""
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return False, None
    return True, float(value)


def parse_open_meteo_daily(body: object, variable: str, ingested_at: datetime) -> FetchResult:
    """Parse an Open-Meteo forecast response with a ``daily`` block into forecast observations.

    A forecast is a model output: kind FORECAST, never verified, and it carries no issuance time,
    so it is available to AMBRE from the moment it was fetched (``available_at = ingested_at``).
    Each fetch is a new revision: earlier forecasts are never overwritten.
    """
    if variable not in get_args(WeatherForecastPayload.model_fields["variable"].annotation):
        raise ValueError(f"unknown weather variable {variable!r}")
    series_key = OPEN_METEO_SERIES[variable]
    inconclusive = FetchResult(kind=ResultKind.INCONCLUSIVE, detail="unexpected response body")
    if not isinstance(body, dict):
        return inconclusive
    daily, lat, lon = body.get("daily"), body.get("latitude"), body.get("longitude")
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        return inconclusive
    times, values = daily["time"], daily.get(series_key)
    if not isinstance(values, list) or len(values) != len(times):
        return inconclusive
    if not all(isinstance(x, int | float) and not isinstance(x, bool) for x in (lat, lon)):
        return inconclusive
    if not times:
        return FetchResult(kind=ResultKind.EMPTY)
    units = body.get("daily_units")
    unit = units.get(series_key) if isinstance(units, dict) else None
    tz = body.get("timezone")
    observations: list[Observation] = []
    invalid = 0
    for raw_day, raw_value in zip(times, values, strict=True):
        ok, value = _finite_or_none(raw_value)
        try:
            day = date.fromisoformat(raw_day) if isinstance(raw_day, str) else None
        except ValueError:
            day = None
        if day is None or not ok:
            invalid += 1
            continue
        try:
            payload = WeatherForecastPayload(
                variable=variable,
                target_date=day.isoformat(),
                value=value,
                unit=unit if isinstance(unit, str) else None,
                latitude=float(lat),  # type: ignore[arg-type]
                longitude=float(lon),  # type: ignore[arg-type]
                timezone=tz if isinstance(tz, str) else None,
            )
        except ValidationError:
            invalid += 1
            continue
        native = f"{float(lat):.4f},{float(lon):.4f}|{variable}|{day.isoformat()}"  # type: ignore[arg-type]
        observations.append(
            Observation(
                observation_id=make_observation_id(OPEN_METEO_ID, native, ingested_at.isoformat()),
                provider_id=OPEN_METEO_ID,
                domain=Domain.WEATHER,
                kind=ObservationKind.FORECAST,
                payload=payload,
                ingested_at=ingested_at,
                verification_status=VerificationStatus.UNVERIFIED,
                title=f"{variable} forecast for {day.isoformat()}",
                event_at=None,
                published_at=None,
                available_at=ingested_at,
                revision=ingested_at.isoformat(),
            )
        )
    if not observations:
        return FetchResult(
            kind=ResultKind.INCONCLUSIVE, detail="every item was invalid", invalid_items=invalid
        )
    return FetchResult(kind=ResultKind.OK, observations=tuple(observations), invalid_items=invalid)

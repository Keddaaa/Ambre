"""Open-Meteo daily JSON -> forecast observations (pure). The fixture shape was seen live on
2026-11-02; the values are hand-written and every id is prefixed synthetic_."""

from datetime import UTC, datetime, timedelta
from typing import Any

from ambre.intelligence.models import (
    Domain,
    ObservationKind,
    ResultKind,
    VerificationStatus,
    WeatherForecastPayload,
    is_actionable,
)
from ambre.intelligence.normalization import parse_open_meteo_daily

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "latitude": 40.710335,
        "longitude": -73.99308,
        "utc_offset_seconds": -18000,
        "timezone": "America/New_York",
        "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
        "daily": {"time": ["2026-11-02", "2026-11-03"], "temperature_2m_max": [18.6, 19.4]},
    }
    data.update(overrides)
    return data


def test_each_day_becomes_one_forecast_observation_with_honest_clocks() -> None:
    r = parse_open_meteo_daily(body(), "temperature_max", NOW)
    assert r.kind is ResultKind.OK and len(r.observations) == 2
    o = r.observations[0]
    assert (o.provider_id, o.domain, o.kind) == (
        "open_meteo",
        Domain.WEATHER,
        ObservationKind.FORECAST,
    )
    p = o.payload
    assert isinstance(p, WeatherForecastPayload)
    assert (p.variable, p.target_date, p.value, p.unit) == (
        "temperature_max",
        "2026-11-02",
        18.6,
        "°C",
    )
    assert (p.latitude, p.longitude, p.timezone) == (40.710335, -73.99308, "America/New_York")
    assert o.event_at is None  # a calendar day is not an instant
    assert o.published_at is None  # the API gives no issuance time of the model run
    assert o.available_at == o.ingested_at == NOW  # readable by AMBRE from the moment it fetched it
    assert o.verification_status is VerificationStatus.UNVERIFIED


def test_a_forecast_is_never_an_actionable_fact() -> None:
    o = parse_open_meteo_daily(body(), "temperature_max", NOW).observations[0]
    assert not is_actionable(o)


def test_every_fetch_is_a_new_observation_so_forecast_history_is_kept() -> None:
    a = parse_open_meteo_daily(body(), "temperature_max", NOW).observations[0]
    b = parse_open_meteo_daily(body(), "temperature_max", NOW + timedelta(hours=1)).observations[0]
    assert a.observation_id != b.observation_id
    assert a.payload == b.payload  # same value, two dated readings
    same = parse_open_meteo_daily(body(), "temperature_max", NOW).observations[0]
    assert a.observation_id == same.observation_id


def test_the_other_variables_use_their_own_series_and_units() -> None:
    rain = body(
        daily_units={"time": "iso8601", "precipitation_sum": "mm"},
        daily={"time": ["2026-11-02"], "precipitation_sum": [0.0]},
    )
    o = parse_open_meteo_daily(rain, "precipitation_sum", NOW).observations[0]
    p = o.payload
    assert isinstance(p, WeatherForecastPayload)
    assert p.value == 0.0 and p.unit == "mm"  # a real zero is kept: it was reported


def test_a_null_value_stays_none_never_zero() -> None:
    nulls = body(daily={"time": ["2026-11-02", "2026-11-03"], "temperature_2m_max": [None, 19.4]})
    first = parse_open_meteo_daily(nulls, "temperature_max", NOW).observations[0].payload
    assert isinstance(first, WeatherForecastPayload) and first.value is None


def test_non_finite_or_malformed_items_are_counted_and_skipped() -> None:
    bad = body(
        daily={
            "time": ["2026-11-02", "not-a-date", "2026-11-04", "2026-11-05"],
            "temperature_2m_max": [18.0, 19.0, float("nan"), 20.0],
        }
    )
    r = parse_open_meteo_daily(bad, "temperature_max", NOW)
    assert [o.payload.target_date for o in r.observations] == ["2026-11-02", "2026-11-05"]  # type: ignore[union-attr]
    assert r.invalid_items == 2


def test_unexpected_bodies_are_inconclusive_not_empty() -> None:
    for broken in (
        {},
        [],
        None,
        body(daily={}),
        body(daily={"time": ["2026-11-02"]}),
        body(daily={"time": ["2026-11-02"], "temperature_2m_max": [1, 2]}),  # lengths differ
        body(latitude=None),
    ):
        r = parse_open_meteo_daily(broken, "temperature_max", NOW)
        assert r.kind is ResultKind.INCONCLUSIVE, broken
        assert r.observations == ()


def test_an_empty_daily_series_is_empty() -> None:
    r = parse_open_meteo_daily(
        body(daily={"time": [], "temperature_2m_max": []}), "temperature_max", NOW
    )
    assert r.kind is ResultKind.EMPTY

"""Open-Meteo provider on a mocked transport: no Internet, GET only, documented parameters only."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from ambre.intelligence.models import Domain, InfoNeed, ProviderCard, ProviderStatus, ResultKind
from ambre.intelligence.providers.open_meteo import OpenMeteoProvider

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CARD = ProviderCard(
    id="open_meteo",
    name="Open-Meteo",
    domain=Domain.WEATHER,
    status=ProviderStatus.VERIFIED_LIMITED,
    enabled=True,
    allow_commercial_use=False,
    capabilities={InfoNeed.WEATHER_FORECAST: 0.8},
)
BODY = {
    "latitude": 40.710335,
    "longitude": -73.99308,
    "timezone": "America/New_York",
    "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
    "daily": {"time": ["2026-11-02", "2026-11-03"], "temperature_2m_max": [18.6, 19.4]},
}


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[OpenMeteoProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return OpenMeteoProvider(CARD, http, clock=lambda: NOW), seen


def fetch(p: OpenMeteoProvider, **kw: object):
    args: dict[str, object] = {
        "latitude": 40.7128,
        "longitude": -74.006,
        "timezone": "America/New_York",
        "variable": "temperature_max",
        "forecast_days": 3,
    }
    args.update(kw)
    return asyncio.run(p.fetch_latest(**args))  # type: ignore[arg-type]


def test_the_request_uses_only_documented_parameters_and_no_key() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    result = fetch(p)
    assert result.kind is ResultKind.OK and len(result.observations) == 2
    (req,) = seen
    assert req.method == "GET"
    assert req.url.host == "api.open-meteo.com" and req.url.path == "/v1/forecast"
    q = dict(req.url.params)
    assert set(q) == {"latitude", "longitude", "daily", "timezone", "forecast_days"}
    assert q["daily"] == "temperature_2m_max" and q["forecast_days"] == "3"
    assert q["timezone"] == "America/New_York"
    assert "AMBRE" in req.headers["user-agent"] and "authorization" not in req.headers
    assert "apikey" not in str(req.url).lower()


def test_each_variable_maps_to_its_open_meteo_series() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    for variable, series in [
        ("temperature_max", "temperature_2m_max"),
        ("temperature_min", "temperature_2m_min"),
        ("precipitation_sum", "precipitation_sum"),
    ]:
        fetch(p, variable=variable)
        assert dict(seen[-1].url.params)["daily"] == series


def test_429_is_failed_with_retry_after_and_is_never_retried() -> None:
    p, seen = provider(lambda r: httpx.Response(429, headers={"Retry-After": "90"}))
    r = fetch(p)
    assert r.kind is ResultKind.FAILED and r.http_status == 429 and r.retry_after_seconds == 90.0
    assert len(seen) == 1


def test_other_failures_are_failed_with_their_status() -> None:
    for status in (400, 500, 503):
        p, _ = provider(lambda r, s=status: httpx.Response(s, json={"reason": "x"}))
        r = fetch(p)
        assert r.kind is ResultKind.FAILED and r.http_status == status


def test_transport_error_and_non_json_are_failed() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert fetch(provider(boom)[0]).kind is ResultKind.FAILED
    assert (
        fetch(provider(lambda r: httpx.Response(200, text="<html>"))[0]).kind is ResultKind.FAILED
    )


def test_an_unexpected_json_body_is_inconclusive() -> None:
    assert (
        fetch(provider(lambda r: httpx.Response(200, json={}))[0]).kind is ResultKind.INCONCLUSIVE
    )


def test_the_documented_horizon_is_enforced_before_any_call() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    for days in (0, 17, -1):
        with pytest.raises(ValueError, match="forecast_days"):
            fetch(p, forecast_days=days)
    assert seen == []


def test_invalid_coordinates_are_refused_before_any_call() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    with pytest.raises(ValueError, match="latitude"):
        fetch(p, latitude=91.0)
    with pytest.raises(ValueError, match="longitude"):
        fetch(p, longitude=float("nan"))
    assert seen == []


def test_search_and_history_are_explicitly_unsupported() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    assert asyncio.run(p.search("x")).kind is ResultKind.UNSUPPORTED
    assert asyncio.run(p.fetch_historical()).kind is ResultKind.UNSUPPORTED
    assert seen == []
    caps = p.get_capabilities()
    assert caps.needs == frozenset({InfoNeed.WEATHER_FORECAST}) and caps.fetch_latest
    assert not caps.search and not caps.fetch_historical


def test_non_commercial_status_is_carried_by_the_card() -> None:
    p, _ = provider(lambda r: httpx.Response(200, json=BODY))
    assert p.card.allow_commercial_use is False

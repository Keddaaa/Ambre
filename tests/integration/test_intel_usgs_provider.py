"""USGS provider against a mocked transport: no Internet, GET only, every failure explicit."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ambre.intelligence.models import Domain, InfoNeed, ProviderCard, ProviderStatus, ResultKind
from ambre.intelligence.providers.usgs import UsgsProvider

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CARD = ProviderCard(
    id="usgs_earthquakes",
    name="USGS",
    domain=Domain.SEISMIC,
    status=ProviderStatus.VERIFIED_FREE,
    enabled=True,
    capabilities={InfoNeed.RECENT_EARTHQUAKES: 0.9},
)
BODY = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "synthetic_eq1",
            "properties": {
                "mag": 6.8,
                "place": "somewhere, New Zealand",
                "time": int((NOW - timedelta(hours=2)).timestamp() * 1000),
                "updated": int((NOW - timedelta(hours=1)).timestamp() * 1000),
                "status": "reviewed",
            },
            "geometry": {"type": "Point", "coordinates": [174.0, -41.0, 10.0]},
        }
    ],
}


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[UsgsProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return UsgsProvider(CARD, http, clock=lambda: NOW), seen


def test_fetch_latest_sends_documented_parameters_with_get_only() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    result = asyncio.run(p.fetch_latest(min_magnitude=6.5, since=NOW - timedelta(days=7), limit=50))
    assert result.kind is ResultKind.OK and len(result.observations) == 1
    (req,) = seen
    assert req.method == "GET"
    assert req.url.host == "earthquake.usgs.gov" and req.url.path == "/fdsnws/event/1/query"
    q = dict(req.url.params)
    assert q["format"] == "geojson" and q["minmagnitude"] == "6.5" and q["limit"] == "50"
    assert q["starttime"].startswith("2026-10-26T12:00:00")
    assert q["orderby"] == "time"
    assert "AMBRE" in req.headers["user-agent"]
    assert "authorization" not in req.headers


def test_fetch_historical_uses_start_and_end() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    asyncio.run(
        p.fetch_historical(
            start=NOW - timedelta(days=3), end=NOW - timedelta(days=1), min_magnitude=5.0
        )
    )
    q = dict(seen[0].url.params)
    assert q["starttime"].startswith("2026-10-30") and q["endtime"].startswith("2026-11-01")


def test_empty_feature_collection_is_empty() -> None:
    p, _ = provider(
        lambda r: httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    )
    r = asyncio.run(p.fetch_latest(min_magnitude=7.0, since=NOW - timedelta(days=1)))
    assert r.kind is ResultKind.EMPTY


def test_no_content_204_is_empty() -> None:
    p, _ = provider(lambda r: httpx.Response(204))
    r = asyncio.run(p.fetch_latest(min_magnitude=7.0, since=NOW - timedelta(days=1)))
    assert r.kind is ResultKind.EMPTY


def test_429_is_failed_with_retry_after() -> None:
    p, _ = provider(lambda r: httpx.Response(429, headers={"Retry-After": "120"}))
    r = asyncio.run(p.fetch_latest(min_magnitude=5.0, since=NOW - timedelta(days=1)))
    assert r.kind is ResultKind.FAILED and r.http_status == 429
    assert r.retry_after_seconds == 120.0


def test_server_error_and_bad_request_are_failed_not_retried() -> None:
    for status in (500, 503, 400):
        p, seen = provider(lambda r, s=status: httpx.Response(s, text="nope"))
        r = asyncio.run(p.fetch_latest(min_magnitude=5.0, since=NOW - timedelta(days=1)))
        assert r.kind is ResultKind.FAILED and r.http_status == status
        assert len(seen) == 1  # no hidden retry: the Budget Controller owns retries


def test_transport_error_is_failed() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    p, _ = provider(boom)
    r = asyncio.run(p.fetch_latest(min_magnitude=5.0, since=NOW - timedelta(days=1)))
    assert r.kind is ResultKind.FAILED and r.http_status is None


def test_non_json_body_is_failed() -> None:
    p, _ = provider(lambda r: httpx.Response(200, text="<html>maintenance</html>"))
    r = asyncio.run(p.fetch_latest(min_magnitude=5.0, since=NOW - timedelta(days=1)))
    assert r.kind is ResultKind.FAILED


def test_json_that_is_not_a_feature_collection_is_inconclusive() -> None:
    p, _ = provider(lambda r: httpx.Response(200, json={}))
    r = asyncio.run(p.fetch_latest(min_magnitude=5.0, since=NOW - timedelta(days=1)))
    assert r.kind is ResultKind.INCONCLUSIVE


def test_limit_above_the_documented_maximum_is_refused_before_any_call() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(p.fetch_latest(min_magnitude=5.0, since=NOW, limit=20001))
    assert seen == []


def test_unsupported_methods_say_so_instead_of_inventing_data() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    r = asyncio.run(p.search("anything"))
    assert r.kind is ResultKind.UNSUPPORTED and r.observations == ()
    assert seen == []


def test_capabilities_and_rate_limit_status_are_explicit() -> None:
    p, _ = provider(lambda r: httpx.Response(200, json=BODY))
    caps = p.get_capabilities()
    assert caps.needs == frozenset({InfoNeed.RECENT_EARTHQUAKES})
    assert caps.fetch_latest and caps.fetch_historical and not caps.search
    status = p.get_rate_limit_status()
    assert status.remaining is None and status.published is False


def test_healthcheck_reports_ok_and_failure() -> None:
    ok, _ = provider(lambda r: httpx.Response(200, json=BODY))
    assert asyncio.run(ok.healthcheck()).ok
    down, _ = provider(lambda r: httpx.Response(503))
    h = asyncio.run(down.healthcheck())
    assert not h.ok and "503" in (h.detail or "")

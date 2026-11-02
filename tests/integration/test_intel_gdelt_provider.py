"""GDELT provider on a mocked transport: GET only, documented parameters, explicit failures."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from ambre.intelligence.models import Domain, InfoNeed, ProviderCard, ProviderStatus, ResultKind
from ambre.intelligence.providers.gdelt import GdeltProvider

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CARD = ProviderCard(
    id="gdelt",
    name="GDELT",
    domain=Domain.NEWS,
    status=ProviderStatus.VERIFIED_LIMITED,
    enabled=True,
    capabilities={InfoNeed.GENERAL_NEWS: 0.5},
)
BODY = {
    "articles": [
        {
            "url": "https://example.invalid/a",
            "title": "OpenAI releases a new model",
            "seendate": "20261101T093000Z",
            "domain": "example.invalid",
            "language": "English",
            "sourcecountry": "United States",
        }
    ]
}


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[GdeltProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return GdeltProvider(
        CARD, httpx.AsyncClient(transport=httpx.MockTransport(wrapped)), clock=lambda: NOW
    ), seen


def search(p: GdeltProvider, query: str = 'OpenAI "new model"', **kw: object):
    return asyncio.run(p.search(query, **kw))  # type: ignore[arg-type]


def test_the_request_uses_only_documented_parameters() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    r = search(p, timespan="1week", max_records=50)
    assert r.kind is ResultKind.OK and len(r.observations) == 1
    (req,) = seen
    assert req.method == "GET" and req.url.host == "api.gdeltproject.org"
    assert req.url.path == "/api/v2/doc/doc"
    q = dict(req.url.params)
    assert q == {
        "query": 'OpenAI "new model"',
        "mode": "ArtList",
        "format": "json",
        "maxrecords": "50",
        "timespan": "1week",
    }
    assert "AMBRE" in req.headers["user-agent"] and "authorization" not in req.headers


def test_an_empty_object_from_gdelt_is_inconclusive() -> None:
    p, _ = provider(lambda r: httpx.Response(200, json={}))
    assert search(p).kind is ResultKind.INCONCLUSIVE


def test_429_is_failed_never_retried_and_carries_the_servers_text() -> None:
    body = "Please limit requests to one every 5 seconds"
    p, seen = provider(lambda r: httpx.Response(429, text=body))
    r = search(p)
    assert r.kind is ResultKind.FAILED and r.http_status == 429
    assert len(seen) == 1


def test_a_plain_text_error_with_status_200_is_failed_not_parsed() -> None:
    p, _ = provider(lambda r: httpx.Response(200, text="The specified phrase is too short."))
    r = search(p)
    assert r.kind is ResultKind.FAILED and "not JSON" in (r.detail or "")


def test_server_errors_and_transport_errors_are_failed() -> None:
    p, _ = provider(lambda r: httpx.Response(503))
    assert search(p).kind is ResultKind.FAILED

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert search(provider(boom)[0]).kind is ResultKind.FAILED


@pytest.mark.parametrize("bad", ["", "   ", "x" * 301, "a\nb", "a\x00b"])
def test_an_invalid_query_is_refused_before_any_call(bad: str) -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    with pytest.raises(ValueError, match="query"):
        search(p, bad)
    assert seen == []


def test_record_and_timespan_limits_are_enforced() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    with pytest.raises(ValueError, match="max_records"):
        search(p, max_records=251)
    with pytest.raises(ValueError, match="max_records"):
        search(p, max_records=0)
    with pytest.raises(ValueError, match="timespan"):
        search(p, timespan="forever")
    assert seen == []


def test_unsupported_methods_and_capabilities_are_explicit() -> None:
    p, seen = provider(lambda r: httpx.Response(200, json=BODY))
    assert asyncio.run(p.fetch_latest()).kind is ResultKind.UNSUPPORTED
    assert asyncio.run(p.fetch_historical()).kind is ResultKind.UNSUPPORTED
    caps = p.get_capabilities()
    assert (
        caps.search and not caps.fetch_latest and caps.needs == frozenset({InfoNeed.GENERAL_NEWS})
    )
    assert seen == []

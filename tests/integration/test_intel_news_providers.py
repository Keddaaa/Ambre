"""The two news connectors against a mocked transport: no network, a controlled clock."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ambre.intelligence.models import ProviderCard, ResultKind
from ambre.intelligence.providers.feed import FeedProvider
from ambre.intelligence.providers.spaceflight_news import (
    ARTICLES_URL,
    SpaceflightNewsProvider,
)
from ambre.intelligence.registry import load_registry

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "news"
CONFIGS = Path(__file__).resolve().parents[2] / "configs"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
FEED_URL = "https://feeds.example/world.xml"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def card(provider_id: str = "synthetic_feed", endpoint: str | None = FEED_URL) -> ProviderCard:
    return ProviderCard.model_validate(
        {
            "id": provider_id,
            "name": "Synthetic World News",
            "domain": "news",
            "endpoint": endpoint,
            "capabilities": {"news_feed": 0.4},
        }
    )


def snapi() -> SpaceflightNewsProvider:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=json.loads((FIX / "synthetic_snapi_page.json").read_text(encoding="utf-8"))
        )

    provider = SpaceflightNewsProvider(card("spaceflight_news", None), client(handler), lambda: NOW)
    provider.seen = seen  # type: ignore[attr-defined]
    return provider


# --------------------------------------------------------------- Spaceflight News


def test_snapi_asks_for_newest_first_with_a_since_bound_and_reads_no_key() -> None:
    provider = snapi()
    since = datetime(2026, 11, 1, tzinfo=UTC)
    result = asyncio.run(provider.fetch_latest(since=since, limit=20))
    request = provider.seen[0]  # type: ignore[attr-defined]
    assert str(request.url).startswith(ARTICLES_URL)
    q = dict(request.url.params)
    assert q["ordering"] == "-published_at" and q["limit"] == "20"
    assert q["published_at_gte"] == "2026-11-01T00:00:00+00:00"
    assert "authorization" not in {k.lower() for k in request.headers}
    assert request.method == "GET"
    assert result.kind is ResultKind.OK and result.http_status == 200
    assert {o.ingested_at for o in result.observations} == {NOW}


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_snapi_rejects_an_out_of_range_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        asyncio.run(snapi().fetch_latest(limit=limit))


def test_snapi_429_is_a_failure_that_carries_retry_after() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    result = asyncio.run(
        SpaceflightNewsProvider(card("spaceflight_news", None), client(handler)).fetch_latest()
    )
    assert result.kind is ResultKind.FAILED and result.http_status == 429
    assert result.retry_after_seconds == 120.0


def test_snapi_transport_error_and_prose_body_are_failures_not_empty() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    def prose(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="maintenance in progress")

    for handler in (boom, prose):
        result = asyncio.run(
            SpaceflightNewsProvider(card("spaceflight_news", None), client(handler)).fetch_latest()
        )
        assert result.kind is ResultKind.FAILED and not result.observations


# --------------------------------------------------------------------- RSS / Atom


def test_a_feed_card_without_an_address_cannot_be_built() -> None:
    with pytest.raises(ValueError):
        FeedProvider(card(endpoint=None), client(lambda r: httpx.Response(200)))


def test_first_poll_is_unconditional_and_keeps_the_validators_for_the_next_one() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(FIX / "synthetic_rss.xml").read_bytes(),
            headers={"ETag": '"v1"', "Last-Modified": "Sun, 01 Nov 2026 09:30:00 GMT"},
        )

    result = asyncio.run(FeedProvider(card(), client(handler), lambda: NOW).fetch_latest())
    assert "if-none-match" not in {k.lower() for k in requests[0].headers}
    assert result.kind is ResultKind.OK and len(result.observations) == 2
    assert result.etag == '"v1"' and result.last_modified == "Sun, 01 Nov 2026 09:30:00 GMT"
    assert result.observations[0].provider_id == "synthetic_feed"


def test_an_unchanged_feed_costs_one_304_and_stores_nothing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(304)

    result = asyncio.run(
        FeedProvider(card(), client(handler), lambda: NOW).fetch_latest(
            etag='"v1"', last_modified="Sun, 01 Nov 2026 09:30:00 GMT"
        )
    )
    assert seen[0].headers["If-None-Match"] == '"v1"'
    assert seen[0].headers["If-Modified-Since"] == "Sun, 01 Nov 2026 09:30:00 GMT"
    assert result.kind is ResultKind.EMPTY and result.http_status == 304
    assert not result.observations and result.etag == '"v1"'  # validators survive a 304


def test_feed_failures_are_failures_and_a_hostile_document_is_refused() -> None:
    cases: list[Callable[[httpx.Request], httpx.Response]] = [
        lambda r: httpx.Response(503),
        lambda r: httpx.Response(200, content=b"<html>login</html>"),
        lambda r: httpx.Response(
            200, content=b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>'
        ),
    ]
    for handler in cases:
        result = asyncio.run(FeedProvider(card(), client(handler)).fetch_latest())
        assert result.kind is ResultKind.FAILED and not result.observations


def test_the_publisher_feeds_in_the_registry_stay_off_until_their_terms_are_verified() -> None:
    registry = load_registry(CONFIGS / "providers.yaml")
    feeds = [c for c in registry.all() if c.connector == "rss_feed"]
    assert {c.id for c in feeds} >= {"bbc_news_world", "guardian_world", "dw_top_stories"}
    assert all(not c.enabled and c.status.value == "UNVERIFIED" for c in feeds)
    snapi_card = registry.get("spaceflight_news")
    assert snapi_card is not None and snapi_card.enabled and snapi_card.provides  # verified API

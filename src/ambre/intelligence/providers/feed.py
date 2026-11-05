"""Generic RSS 2.0 / Atom 1.0 adapter with conditional requests.

One adapter serves every feed; the feed address comes from the registry card (``endpoint``).
A feed is polled with ``If-None-Match`` / ``If-Modified-Since`` when the server gave validators
last time, so an unchanged feed costs one tiny ``304`` and stores nothing. The adapter reads
titles, short summaries and links only: it is not a full-text news API, and every item is an
unverified report. A feed is used only if its card is enabled, which the registry allows only
for a verified status; the terms of each publisher are recorded in ``docs/API_CATALOG.md``.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ambre.intelligence.models import FetchResult, ProviderCard, ResultKind
from ambre.intelligence.news_parsing import FeedParseError, parse_feed
from ambre.intelligence.providers.base import Capabilities, Health, Provider, RateLimitStatus

USER_AGENT = "AMBRE-research/0.2 (read-only paper-trading research; github.com/Keddaaa/Ambre)"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 else None


class FeedProvider(Provider):
    def __init__(
        self,
        card: ProviderCard,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(card)
        if not card.endpoint or not card.endpoint.startswith(("http://", "https://")):
            raise ValueError(f"{card.id}: a feed card needs an http(s) endpoint")
        self._http = http
        self._clock = clock

    async def fetch_latest(
        self, *, etag: str | None = None, last_modified: str | None = None, **_: Any
    ) -> FetchResult:
        assert self.card.endpoint is not None
        headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            response = await self._http.get(self.card.endpoint, headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            return FetchResult(
                kind=ResultKind.FAILED, detail=f"transport error: {type(exc).__name__}"
            )
        status = response.status_code
        new_etag = response.headers.get("ETag") or etag
        new_modified = response.headers.get("Last-Modified") or last_modified
        if status == 304:
            return FetchResult(
                kind=ResultKind.EMPTY,
                detail="not modified",
                http_status=status,
                etag=new_etag,
                last_modified=new_modified,
            )
        if status != 200:
            return FetchResult(
                kind=ResultKind.FAILED,
                detail=f"HTTP {status}",
                http_status=status,
                retry_after_seconds=_retry_after(response),
            )
        try:
            parsed = parse_feed(
                response.content, self.card.id, self._clock(), source_name=self.card.name
            )
        except FeedParseError as exc:
            return FetchResult(kind=ResultKind.FAILED, detail=f"unusable feed: {exc}")
        return parsed.model_copy(
            update={"http_status": status, "etag": new_etag, "last_modified": new_modified}
        )

    async def healthcheck(self) -> Health:
        result = await self.fetch_latest()
        if result.kind in (ResultKind.OK, ResultKind.EMPTY):
            return Health(ok=True)
        return Health(ok=False, detail=result.detail)

    def get_capabilities(self) -> Capabilities:
        return Capabilities(needs=frozenset(self.card.capabilities), fetch_latest=True)

    def get_rate_limit_status(self) -> RateLimitStatus:
        return RateLimitStatus(published=False)

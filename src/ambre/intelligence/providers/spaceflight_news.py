"""Spaceflight News API v4 (The Space Devs), read-only.

Verified 2026-11-02 against the published OpenAPI schema: ``GET /v4/articles/`` needs no key,
accepts ``ordering``, ``limit`` and ``published_at_gte``, and returns a paginated list. The
service states it is free to use and publishes no rate limit and no licence for the article
data, so AMBRE keeps the lowest budget it can justify, stores only the title, a short summary,
the link and the publisher's name, and shows the publisher wherever an item is shown.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ambre.intelligence.models import FetchResult, ProviderCard, ResultKind
from ambre.intelligence.news_parsing import parse_snapi_articles
from ambre.intelligence.providers.base import Capabilities, Health, Provider, RateLimitStatus

ARTICLES_URL = "https://api.spaceflightnewsapi.net/v4/articles/"
USER_AGENT = "AMBRE-research/0.2 (read-only paper-trading research; github.com/Keddaaa/Ambre)"
MAX_LIMIT = 100
DEFAULT_LIMIT = 50


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 else None


class SpaceflightNewsProvider(Provider):
    def __init__(
        self,
        card: ProviderCard,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(card)
        self._http = http
        self._clock = clock

    async def fetch_latest(
        self, *, since: datetime | None = None, limit: int = DEFAULT_LIMIT, **_: Any
    ) -> FetchResult:
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be in [1, {MAX_LIMIT}], got {limit}")
        params = {"ordering": "-published_at", "limit": str(limit)}
        if since is not None:
            params["published_at_gte"] = since.astimezone(UTC).isoformat()
        try:
            response = await self._http.get(
                ARTICLES_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30.0
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                kind=ResultKind.FAILED, detail=f"transport error: {type(exc).__name__}"
            )
        status = response.status_code
        if status != 200:
            return FetchResult(
                kind=ResultKind.FAILED,
                detail=f"HTTP {status}",
                http_status=status,
                retry_after_seconds=_retry_after(response),
            )
        try:
            body: Any = response.json()
        except ValueError:
            return FetchResult(
                kind=ResultKind.FAILED, detail="response is not JSON", http_status=status
            )
        return parse_snapi_articles(body, self._clock()).model_copy(update={"http_status": status})

    async def healthcheck(self) -> Health:
        result = await self.fetch_latest(limit=1)
        if result.kind in (ResultKind.OK, ResultKind.EMPTY):
            return Health(ok=True)
        return Health(ok=False, detail=result.detail)

    def get_capabilities(self) -> Capabilities:
        return Capabilities(needs=frozenset(self.card.capabilities), fetch_latest=True)

    def get_rate_limit_status(self) -> RateLimitStatus:
        return RateLimitStatus(published=False)  # the API publishes no limit

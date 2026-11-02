"""GDELT DOC 2.0 article search: the generalist source (level 2). GET only, no key, no hidden retry.

Verified 2026-11-02 against the documentation and live reads (docs/API_CATALOG.md): endpoint
``/api/v2/doc/doc`` with ``query``, ``mode=ArtList``, ``format=json``, ``maxrecords`` (default
75, maximum 250) and ``timespan`` (at least 15 minutes). The server asks for one request every
5 seconds and answers HTTP 429 beyond that: the Budget Controller enforces a longer interval.
GDELT answers ``{}`` for several different causes, which proves neither data nor its absence.
Attribution to GDELT is required where results are shown.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ambre.intelligence.models import FetchResult, ProviderCard, ResultKind
from ambre.intelligence.normalization import parse_gdelt_articles
from ambre.intelligence.providers.base import Capabilities, Health, Provider, RateLimitStatus

SEARCH_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 250
DEFAULT_RECORDS = 75
MAX_QUERY = 300
USER_AGENT = "AMBRE-research/0.2 (read-only paper-trading research; github.com/Keddaaa/Ambre)"
_TIMESPAN = re.compile(r"^\d+(min|h|hours?|d|days?|w|weeks?|m|months?)$", re.IGNORECASE)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GdeltProvider(Provider):
    def __init__(
        self,
        card: ProviderCard,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(card)
        self._http = http
        self._clock = clock

    async def search(  # type: ignore[override]
        self,
        query: str,
        *,
        timespan: str = "1week",
        max_records: int = DEFAULT_RECORDS,
    ) -> FetchResult:
        text = query.strip()
        if not text or len(text) > MAX_QUERY or any(ord(c) < 32 for c in text):
            raise ValueError(f"invalid query (1..{MAX_QUERY} printable characters): {query!r}")
        if not 1 <= max_records <= MAX_RECORDS:
            raise ValueError(f"max_records must be in [1, {MAX_RECORDS}], got {max_records}")
        if not _TIMESPAN.match(timespan):
            raise ValueError(f"invalid timespan {timespan!r}")
        params = {
            "query": text,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max_records),
            "timespan": timespan,
        }
        try:
            response = await self._http.get(
                SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=45.0
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                kind=ResultKind.FAILED, detail=f"transport error: {type(exc).__name__}"
            )
        status = response.status_code
        if status != 200:
            return FetchResult(kind=ResultKind.FAILED, detail=f"HTTP {status}", http_status=status)
        try:
            body: Any = response.json()
        except ValueError:
            # e.g. "The specified phrase is too short.": an answer in prose, not data
            return FetchResult(
                kind=ResultKind.FAILED, detail="response is not JSON", http_status=status
            )
        result = parse_gdelt_articles(body, self._clock())
        return result.model_copy(update={"http_status": status})

    async def healthcheck(self) -> Health:
        result = await self.search("news", timespan="1h", max_records=1)
        if result.kind in (ResultKind.OK, ResultKind.EMPTY, ResultKind.INCONCLUSIVE):
            return Health(ok=True)
        return Health(ok=False, detail=result.detail)

    def get_capabilities(self) -> Capabilities:
        return Capabilities(needs=frozenset(self.card.capabilities), search=True)

    def get_rate_limit_status(self) -> RateLimitStatus:
        return RateLimitStatus(published=False)  # the server states 1 request / 5 s in its 429 text

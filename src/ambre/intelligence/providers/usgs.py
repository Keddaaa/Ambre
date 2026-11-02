"""USGS Earthquake Catalog client (FDSN event service). GET only, no key, no hidden retry.

Verified 2026-11-02 against the official documentation and a live read (docs/API_CATALOG.md).
Only documented query parameters are used: format, starttime, endtime, minmagnitude, limit,
orderby. A failure is returned as an explicit result; waiting and retrying belong to the
Budget Controller, so a provider can never hammer an API on its own.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ambre.intelligence.models import FetchResult, ProviderCard, ResultKind
from ambre.intelligence.normalization import parse_usgs_geojson
from ambre.intelligence.providers.base import Capabilities, Health, Provider, RateLimitStatus

QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
MAX_LIMIT = 20000  # documented per-request maximum; larger queries return HTTP 400
USER_AGENT = "AMBRE-research/0.2 (read-only paper-trading research; github.com/Keddaaa/Ambre)"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


class UsgsProvider(Provider):
    def __init__(
        self,
        card: ProviderCard,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(card)
        self._http = http
        self._clock = clock

    async def _query(self, params: dict[str, str]) -> FetchResult:
        try:
            response = await self._http.get(
                QUERY_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30.0
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                kind=ResultKind.FAILED, detail=f"transport error: {type(exc).__name__}"
            )
        status = response.status_code
        if status == 204:  # FDSN convention: no matching event
            return FetchResult(kind=ResultKind.EMPTY, http_status=status)
        if status != 200:
            return FetchResult(
                kind=ResultKind.FAILED,
                detail=f"HTTP {status}",
                http_status=status,
                retry_after_seconds=_retry_after(response) if status == 429 else None,
            )
        try:
            body: Any = response.json()
        except ValueError:
            return FetchResult(
                kind=ResultKind.FAILED, detail="response is not JSON", http_status=status
            )
        result = parse_usgs_geojson(body, self._clock())
        return result.model_copy(update={"http_status": status})

    @staticmethod
    def _params(
        min_magnitude: float, limit: int, start: datetime, end: datetime | None
    ) -> dict[str, str]:
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be in [1, {MAX_LIMIT}], got {limit}")
        params = {
            "format": "geojson",
            "starttime": _iso(start),
            "minmagnitude": f"{min_magnitude:g}",
            "limit": str(limit),
            "orderby": "time",
        }
        if end is not None:
            params["endtime"] = _iso(end)
        return params

    async def fetch_latest(  # type: ignore[override]
        self, *, min_magnitude: float, since: datetime, limit: int = 200
    ) -> FetchResult:
        return await self._query(self._params(min_magnitude, limit, since, None))

    async def fetch_historical(  # type: ignore[override]
        self, *, start: datetime, end: datetime, min_magnitude: float, limit: int = 1000
    ) -> FetchResult:
        return await self._query(self._params(min_magnitude, limit, start, end))

    async def healthcheck(self) -> Health:
        result = await self._query(
            self._params(
                min_magnitude=8.0, limit=1, start=self._clock().replace(microsecond=0), end=None
            )
        )
        if result.kind in (ResultKind.OK, ResultKind.EMPTY):
            return Health(ok=True)
        return Health(ok=False, detail=result.detail)

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            needs=frozenset(self.card.capabilities), fetch_latest=True, fetch_historical=True
        )

    def get_rate_limit_status(self) -> RateLimitStatus:
        return RateLimitStatus(published=False)  # USGS publishes no rate-limit figures

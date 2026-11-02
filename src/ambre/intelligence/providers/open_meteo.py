"""Open-Meteo forecast client. GET only, no key, no hidden retry, documented parameters only.

Verified 2026-11-02 against the official documentation and live reads (docs/API_CATALOG.md):
``/v1/forecast`` with ``latitude``, ``longitude``, ``daily``, ``timezone`` and ``forecast_days``
(at most 16). The free API is for non-commercial use only; the registry card says so.
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ambre.intelligence.models import FetchResult, ProviderCard, ResultKind
from ambre.intelligence.normalization import OPEN_METEO_SERIES, parse_open_meteo_daily
from ambre.intelligence.providers.base import Capabilities, Health, Provider, RateLimitStatus

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_FORECAST_DAYS = 16
USER_AGENT = "AMBRE-research/0.2 (read-only paper-trading research; github.com/Keddaaa/Ambre)"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


class OpenMeteoProvider(Provider):
    def __init__(
        self,
        card: ProviderCard,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(card)
        self._http = http
        self._clock = clock

    async def fetch_latest(  # type: ignore[override]
        self,
        *,
        latitude: float,
        longitude: float,
        timezone: str,
        variable: str,
        forecast_days: int,
    ) -> FetchResult:
        if not (math.isfinite(latitude) and -90 <= latitude <= 90):
            raise ValueError(f"latitude out of range: {latitude}")
        if not (math.isfinite(longitude) and -180 <= longitude <= 180):
            raise ValueError(f"longitude out of range: {longitude}")
        if not 1 <= forecast_days <= MAX_FORECAST_DAYS:
            raise ValueError(
                f"forecast_days must be in [1, {MAX_FORECAST_DAYS}], got {forecast_days}"
            )
        if variable not in OPEN_METEO_SERIES:
            raise ValueError(f"unknown weather variable {variable!r}")
        params = {
            "latitude": f"{latitude:g}",
            "longitude": f"{longitude:g}",
            "daily": OPEN_METEO_SERIES[variable],
            "timezone": timezone,
            "forecast_days": str(forecast_days),
        }
        try:
            response = await self._http.get(
                FORECAST_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30.0
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
                retry_after_seconds=_retry_after(response) if status == 429 else None,
            )
        try:
            body: Any = response.json()
        except ValueError:
            return FetchResult(
                kind=ResultKind.FAILED, detail="response is not JSON", http_status=status
            )
        result = parse_open_meteo_daily(body, variable, self._clock())
        return result.model_copy(update={"http_status": status})

    async def healthcheck(self) -> Health:
        result = await self.fetch_latest(
            latitude=0.0, longitude=0.0, timezone="UTC", variable="temperature_max", forecast_days=1
        )
        if result.kind in (ResultKind.OK, ResultKind.EMPTY):
            return Health(ok=True)
        return Health(ok=False, detail=result.detail)

    def get_capabilities(self) -> Capabilities:
        return Capabilities(needs=frozenset(self.card.capabilities), fetch_latest=True)

    def get_rate_limit_status(self) -> RateLimitStatus:
        # Documented limits exist (docs/API_LIMITS.md) but the API reports no remaining count.
        return RateLimitStatus(published=False)

"""Read-only Manifold Markets client with retry, exponential backoff and full jitter."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

MAX_LIST_LIMIT = 1000


class ManifoldError(Exception):
    """Any failure talking to Manifold (non-retryable response, bad body)."""


class ManifoldUnavailable(ManifoldError):
    """Retries exhausted on 429, 5xx or transport errors."""


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


class ManifoldClient:
    """GET-only client. It exposes no write method by design."""

    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient | None = None,
        max_retries: int = 5,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_http = http is None
        self._http = http if http is not None else httpx.AsyncClient(timeout=30.0)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep

    async def list_markets(
        self, *, limit: int = MAX_LIST_LIMIT, before: str | None = None
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValueError(f"limit must be in [1, {MAX_LIST_LIMIT}], got {limit}")
        params: dict[str, str] = {"limit": str(limit), "sort": "updated-time", "order": "desc"}
        if before is not None:
            params["before"] = before
        body = await self._get_json("/v0/markets", params)
        if not isinstance(body, list):
            raise ManifoldError(f"expected a JSON list from /v0/markets, got {type(body).__name__}")
        return body

    async def get_market(self, market_id: str) -> dict[str, Any]:
        body = await self._get_json(f"/v0/market/{market_id}", None)
        if not isinstance(body, dict):
            raise ManifoldError(f"expected a JSON object for market, got {type(body).__name__}")
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _backoff(self, attempt: int) -> float:
        return random.uniform(0, min(self._max_delay, self._base_delay * 2**attempt))

    async def _get_json(self, path: str, params: dict[str, str] | None) -> Any:
        url = f"{self._base_url}{path}"
        last: str = ""
        for attempt in range(self._max_retries + 1):
            delay: float | None = None
            try:
                response = await self._http.get(url, params=params)
            except httpx.TransportError as exc:
                last = f"transport error: {exc!r}"
            else:
                status = response.status_code
                if status == 429 or status >= 500:
                    last = f"HTTP {status}"
                    retry_after = _retry_after(response)
                    if retry_after is not None:
                        delay = min(retry_after, self._max_delay)
                elif status >= 400:
                    raise ManifoldError(f"HTTP {status} for GET {path}")
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ManifoldError(f"invalid JSON from GET {path}") from exc
            if attempt == self._max_retries:
                break
            await self._sleep(delay if delay is not None else self._backoff(attempt))
        raise ManifoldUnavailable(
            f"GET {path} failed after {self._max_retries + 1} attempts ({last})"
        )

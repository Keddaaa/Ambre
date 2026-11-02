"""Common provider contract. Methods a provider does not support return an explicit
``UNSUPPORTED`` result, never invented data."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ambre.intelligence.models import FetchResult, InfoNeed, ProviderCard, ResultKind


@dataclass(frozen=True)
class Capabilities:
    needs: frozenset[InfoNeed]
    fetch_latest: bool = False
    search: bool = False
    fetch_historical: bool = False


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class RateLimitStatus:
    """What the provider itself reports. ``published`` is False when it reports nothing: the
    remaining budget then comes only from AMBRE's own usage log (Budget Controller)."""

    published: bool
    remaining: int | None = None
    reset_at: datetime | None = None


def unsupported(method: str, provider_id: str) -> FetchResult:
    return FetchResult(
        kind=ResultKind.UNSUPPORTED, detail=f"{provider_id} does not support {method}"
    )


class Provider:
    card: ProviderCard

    def __init__(self, card: ProviderCard) -> None:
        self.card = card

    async def fetch_latest(self, **params: Any) -> FetchResult:
        return unsupported("fetch_latest", self.card.id)

    async def search(self, query: str, **params: Any) -> FetchResult:
        return unsupported("search", self.card.id)

    async def fetch_historical(self, **params: Any) -> FetchResult:
        return unsupported("fetch_historical", self.card.id)

    async def healthcheck(self) -> Health:
        return Health(ok=False, detail="healthcheck not implemented")

    def get_capabilities(self) -> Capabilities:
        return Capabilities(needs=frozenset(self.card.capabilities))

    def get_rate_limit_status(self) -> RateLimitStatus:
        return RateLimitStatus(published=False)

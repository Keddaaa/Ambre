"""Network-free stand-ins shared by the e2e and CLI tests."""

import copy
import json
from pathlib import Path
from typing import Any

from ambre.integrations.manifold.client import ManifoldUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "manifold"


def load_fixture(name: str = "markets_updated_page.json") -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


class FakeManifoldClient:
    """Serves markets in updated-time desc order with `before` cursors, like /v0/markets."""

    def __init__(self, markets: list[dict[str, Any]], fail: bool = False) -> None:
        self.markets = markets
        self.fail = fail
        self.closed = False

    async def list_markets(self, *, limit: int, before: str | None = None) -> list[dict[str, Any]]:
        if self.fail:
            raise ManifoldUnavailable("GET /v0/markets failed after 6 attempts (HTTP 503)")
        ordered = sorted(self.markets, key=lambda m: m.get("lastUpdatedTime") or 0, reverse=True)
        start = 0 if before is None else [m["id"] for m in ordered].index(before) + 1
        return copy.deepcopy(ordered[start : start + limit])

    async def aclose(self) -> None:
        self.closed = True

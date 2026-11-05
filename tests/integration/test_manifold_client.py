import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from ambre.integrations.manifold.client import (
    ManifoldClient,
    ManifoldError,
    ManifoldUnavailable,
)

BASE = "https://api.manifold.markets"


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request, int], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request, len(self.requests))

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)


def make_client(rec: Recorder, **kwargs: Any) -> ManifoldClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    return ManifoldClient(BASE, http=http, sleep=rec.sleep, **kwargs)


async def test_retries_429_then_succeeds() -> None:
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(429) if n < 3 else httpx.Response(200, json=[{"id": "a"}])

    rec = Recorder(handler)
    client = make_client(rec, max_delay=30.0)
    assert await client.list_markets() == [{"id": "a"}]
    assert len(rec.requests) == 3
    assert len(rec.sleeps) == 2
    assert all(0 <= s <= 30.0 for s in rec.sleeps)


async def test_retry_after_header_respected() -> None:
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=[])

    rec = Recorder(handler)
    await make_client(rec).list_markets()
    assert rec.sleeps == [2.0]


async def test_retry_after_capped_by_max_delay() -> None:
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(503, headers={"Retry-After": "9999"})
        return httpx.Response(200, json=[])

    rec = Recorder(handler)
    await make_client(rec, max_delay=5.0).list_markets()
    assert rec.sleeps == [5.0]


async def test_gives_up_after_max_retries() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(503))
    client = make_client(rec, max_retries=3)
    with pytest.raises(ManifoldUnavailable):
        await client.list_markets()
    assert len(rec.requests) == 4
    assert len(rec.sleeps) == 3


async def test_transport_error_retried() -> None:
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=[])

    rec = Recorder(handler)
    assert await make_client(rec).list_markets() == []
    assert len(rec.requests) == 2


async def test_transport_error_exhausted() -> None:
    def handler(_: httpx.Request, __: int) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    rec = Recorder(handler)
    with pytest.raises(ManifoldUnavailable):
        await make_client(rec, max_retries=2).get_market("abc")
    assert len(rec.requests) == 3


async def test_backoff_bounds_grow_exponentially() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(500))
    client = make_client(rec, max_retries=4, base_delay=1.0, max_delay=3.0)
    with pytest.raises(ManifoldUnavailable):
        await client.list_markets()
    caps = [1.0, 2.0, 3.0, 3.0]
    assert all(0 <= s <= c for s, c in zip(rec.sleeps, caps, strict=True))


async def test_400_not_retried() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(ManifoldError) as exc:
        await make_client(rec).list_markets()
    assert not isinstance(exc.value, ManifoldUnavailable)
    assert len(rec.requests) == 1
    assert rec.sleeps == []


async def test_404_not_retried() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(404, json={"error": "nope"}))
    with pytest.raises(ManifoldError):
        await make_client(rec).get_market("missing")
    assert len(rec.requests) == 1


async def test_non_list_body_rejected() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, json={"error": "oops"}))
    with pytest.raises(ManifoldError):
        await make_client(rec).list_markets()
    assert len(rec.requests) == 1


async def test_invalid_json_rejected() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, content=b"<html>"))
    with pytest.raises(ManifoldError):
        await make_client(rec).list_markets()


async def test_list_markets_params() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, json=[]))
    await make_client(rec).list_markets(limit=50, before="X1")
    req = rec.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v0/markets"
    params = dict(req.url.params)
    assert params == {"limit": "50", "sort": "updated-time", "order": "desc", "before": "X1"}


async def test_list_markets_without_before() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, json=[]))
    await make_client(rec).list_markets()
    params = dict(rec.requests[0].url.params)
    assert "before" not in params
    assert params["limit"] == "1000"
    assert params["sort"] == "updated-time"
    assert params["order"] == "desc"


async def test_list_markets_limit_validated() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        await make_client(rec).list_markets(limit=1001)
    assert rec.requests == []


async def test_get_market() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, json={"id": "abc"}))
    assert await make_client(rec).get_market("abc") == {"id": "abc"}
    assert rec.requests[0].url.path == "/v0/market/abc"


async def test_get_market_non_dict_rejected() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, content=json.dumps([1]).encode()))
    with pytest.raises(ManifoldError):
        await make_client(rec).get_market("abc")


async def test_client_is_read_only() -> None:
    public = {n for n in dir(ManifoldClient) if not n.startswith("_")}
    assert public == {
        "list_markets",
        "get_market",
        "list_bets",
        "search_resolved",
        "aclose",
    }  # every one is a GET


async def test_aclose_closes_owned_client_only() -> None:
    rec = Recorder(lambda _r, _n: httpx.Response(200, json=[]))
    injected = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    await ManifoldClient(BASE, http=injected).aclose()
    assert not injected.is_closed
    await injected.aclose()
    owned = ManifoldClient(BASE)
    await owned.aclose()


# ------------------------------------------------------------------ bets (read-only history)


async def test_list_bets_is_a_plain_get_with_the_market_and_cursor() -> None:
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "b1"}])

    rec = Recorder(handler)
    client = make_client(rec)
    assert await client.list_bets("m1", before="b9", limit=500) == [{"id": "b1"}]
    request = rec.requests[0]
    assert request.method == "GET" and request.url.path == "/v0/bets"
    assert dict(request.url.params) == {"contractId": "m1", "limit": "500", "before": "b9"}


@pytest.mark.parametrize("limit", [0, 1001])
async def test_list_bets_rejects_an_out_of_range_limit(limit: int) -> None:
    client = make_client(Recorder(lambda r, n: httpx.Response(200, json=[])))
    with pytest.raises(ValueError):
        await client.list_bets("m1", limit=limit)


async def test_list_bets_refuses_a_body_that_is_not_a_list() -> None:
    client = make_client(Recorder(lambda r, n: httpx.Response(200, json={"error": "x"})))
    with pytest.raises(ManifoldError):
        await client.list_bets("m1")

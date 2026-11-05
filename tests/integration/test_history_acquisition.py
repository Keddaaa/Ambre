"""Fetching, storing and evaluating the bet history of resolved markets, offline."""

import asyncio
import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from builders import T_SYNC, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre import cli
from ambre.data_engine.history_fetch import fetch_bet_history, fetch_histories
from ambre.evaluation_engine.history import price_at
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import (
    Base,
    DecisionRow,
    LedgerRow,
    PriceHistoryStateRow,
    PricePointRow,
)
from ambre.integrations.manifold.client import ManifoldClient

BASE = "https://api.manifold.markets"
NOW = T_SYNC + timedelta(days=1)
CREATED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def resolved_market(base: dict[str, Any], mid: str, outcome: str, days: float) -> dict[str, Any]:
    m = copy.deepcopy(base)
    m.update(
        id=mid,
        question=f"Will alpha{mid} beta{mid} gamma{mid} happen?",
        slug=mid,
        url=f"https://example.invalid/{mid}",
        isResolved=True,
        resolution=outcome,
        createdTime=ms(CREATED),
        resolutionTime=ms(CREATED + timedelta(days=days)),
        lastUpdatedTime=ms(T_SYNC),
        closeTime=ms(CREATED + timedelta(days=days)),
    )
    return m


def bets_for(market_id: str, n: int) -> list[dict[str, Any]]:
    """n bets, newest first as the API returns them; price rises by 0.001 per bet."""
    out = []
    for i in range(n):
        out.append(
            {
                "id": f"{market_id}-b{i:05d}",
                "contractId": market_id,
                "createdTime": ms(CREATED + timedelta(minutes=10 * (i + 1))),
                "probBefore": 0.5 + 0.001 * i,
                "probAfter": 0.5 + 0.001 * (i + 1),
                "isCancelled": False,
                "isRedemption": False,
            }
        )
    return list(reversed(out))


class Api:
    """A scripted /v0/bets: pages of a per-market list, newest first, cursor = last id seen."""

    def __init__(self, bets: dict[str, list[dict[str, Any]]]) -> None:
        self.bets = bets
        self.requests: list[httpx.Request] = []
        self.fail: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET" and request.url.path == "/v0/bets"
        q = dict(request.url.params)
        if q["contractId"] in self.fail:
            return httpx.Response(500)
        rows = self.bets[q["contractId"]]
        if "before" in q:
            ids = [r["id"] for r in rows]
            rows = rows[ids.index(q["before"]) + 1 :]
        return httpx.Response(200, json=rows[: int(q["limit"])])

    def client(self) -> ManifoldClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

        async def no_sleep(_: float) -> None:
            return None

        return ManifoldClient(BASE, http=http, max_retries=1, sleep=no_sleep)


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'h.db'}")
    Base.metadata.create_all(engine)
    f = make_session_factory(engine)
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    sync(
        f,
        [
            resolved_market(base, "synthetic_big", "YES", 20),
            resolved_market(base, "synthetic_small", "NO", 10),
            resolved_market(base, "synthetic_down", "YES", 5),
            resolved_market(base, "synthetic_huge", "NO", 8),
        ],
        T_SYNC,
    )
    yield f
    engine.dispose()


def api() -> Api:
    return Api(
        {
            "synthetic_big": bets_for("synthetic_big", 2500),
            "synthetic_small": bets_for("synthetic_small", 40),
            "synthetic_down": bets_for("synthetic_down", 3),
            "synthetic_huge": bets_for("synthetic_huge", 2500),
        }
    )


def count(f: sessionmaker[Session], model: type) -> int:
    with f() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


# ------------------------------------------------------------------ pagination


def test_every_page_is_read_until_the_first_bet_and_nothing_is_missing() -> None:
    a = api()
    bets, complete, pages = asyncio.run(fetch_bet_history(a.client(), "synthetic_big"))
    assert complete and pages == 3 and len(bets) == 2500
    assert len({b["id"] for b in bets}) == 2500  # no bet read twice at a page boundary
    assert all(r.method == "GET" for r in a.requests)


def test_hitting_the_page_cap_is_an_incomplete_history_not_a_silent_truncation() -> None:
    a = api()
    bets, complete, pages = asyncio.run(fetch_bet_history(a.client(), "synthetic_big", max_pages=2))
    assert not complete and pages == 2 and len(bets) == 2000


# ------------------------------------------------------------------ storing


def test_only_resolved_yes_no_markets_without_history_are_asked_for(
    factory: sessionmaker[Session],
) -> None:
    with factory() as s:
        ids = repo.markets_needing_history(s, 10)
        assert sorted(ids) == [
            "synthetic_big",
            "synthetic_down",
            "synthetic_huge",
            "synthetic_small",
        ]
        assert ids[0] == "synthetic_big"  # the most recently resolved first
        assert len(repo.markets_needing_history(s, 2)) == 2


def test_fetching_stores_prices_the_evaluation_can_read_and_never_twice(
    factory: sessionmaker[Session],
) -> None:
    a = api()
    report = asyncio.run(fetch_histories(a.client(), factory, lambda: NOW, limit=10))
    assert report.requested == 4 and report.complete == 4 and report.failed == []
    assert report.bets == 2500 * 2 + 40 + 3
    with factory() as s:
        markets = {m.market_id: m for m in repo.historical_markets(s)}
        small = markets["synthetic_small"]
        assert small.outcome is False and small.history.complete
        # the price after 30 minutes is the third bet's probAfter, nothing later
        assert price_at(small.history, CREATED + timedelta(minutes=30)) == pytest.approx(0.503)
        assert price_at(small.history, CREATED + timedelta(minutes=5)) == pytest.approx(0.5)
    n_requests = len(a.requests)
    again = asyncio.run(fetch_histories(a.client(), factory, lambda: NOW, limit=10))
    assert again.requested == 0 and len(a.requests) == n_requests


def test_one_failing_market_is_recorded_and_the_others_still_run(
    factory: sessionmaker[Session],
) -> None:
    a = api()
    a.fail.add("synthetic_small")
    report = asyncio.run(fetch_histories(a.client(), factory, lambda: NOW, limit=10))
    assert report.complete == 3 and [f["market_id"] for f in report.failed] == ["synthetic_small"]
    with factory() as s:
        assert s.get(PriceHistoryStateRow, "synthetic_small") is None  # retried next time
        assert "synthetic_small" in repo.markets_needing_history(s, 10)


def test_an_incomplete_history_is_stored_for_the_record_but_never_evaluated(
    factory: sessionmaker[Session],
) -> None:
    a = api()
    report = asyncio.run(fetch_histories(a.client(), factory, lambda: NOW, limit=10, max_pages=2))
    assert report.incomplete == 2 and report.complete == 2
    with factory() as s:
        state = s.get(PriceHistoryStateRow, "synthetic_big")
        assert state is not None and state.complete is False and state.bet_count == 2000
        markets = {m.market_id: m for m in repo.historical_markets(s)}
        assert price_at(markets["synthetic_big"].history, CREATED + timedelta(days=5)) is None
        assert "synthetic_big" not in repo.markets_needing_history(s, 10)  # not hammered again


def test_saving_a_history_again_replaces_it_instead_of_duplicating(
    factory: sessionmaker[Session],
) -> None:
    a = api()
    asyncio.run(fetch_histories(a.client(), factory, lambda: NOW, limit=10))
    points = count(factory, PricePointRow)
    with factory() as s:
        market = next(m for m in repo.historical_markets(s) if m.market_id == "synthetic_small")
        repo.save_price_history(s, market.history, 40, NOW)
        s.commit()
    assert count(factory, PricePointRow) == points


def test_fetching_history_never_touches_decisions_or_the_ledger(
    factory: sessionmaker[Session],
) -> None:
    asyncio.run(fetch_histories(api().client(), factory, lambda: NOW, limit=10))
    assert count(factory, DecisionRow) == 0 and count(factory, LedgerRow) == 0


# ------------------------------------------------------------------ command line


def test_cli_history_commands_fetch_then_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init-db"]) == 0
    engine = make_engine(url)
    f = make_session_factory(engine)
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    sync(f, [resolved_market(base, "synthetic_small", "NO", 10)], T_SYNC)
    engine.dispose()
    capsys.readouterr()

    a = Api({"synthetic_small": bets_for("synthetic_small", 40)})
    assert cli.main(["history-fetch", "--limit", "5"], client_factory=lambda s: a.client()) == 0
    out = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert out["requested"] == 1 and out["complete"] == 1 and out["failed"] == []

    assert cli.main(["history-eval", "--horizons", "1,3"]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert [h["horizon_days"] for h in report["horizons"]] == [1, 3]
    assert report["context_model"]["status"] == "NOT_EVALUATED"
    assert "folds" not in report["horizons"][0]  # the summary does not dump every observation
    assert report["horizons"][0]["n_observations"] == 1
    assert report["horizons"][0]["n_common"] == 0  # one market: nothing to learn from yet


def test_history_fetch_uses_only_get_requests() -> None:
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert "history_fetch" in src
    fetch_src = (Path(cli.__file__).parent / "data_engine" / "history_fetch.py").read_text(
        encoding="utf-8"
    )
    for verb in ("post(", "put(", "delete(", "patch("):
        assert verb not in fetch_src.lower()


# ------------------------------------------------------------------ the resolved universe


def lite(
    base: dict[str, Any], mid: str, outcome: str, created: datetime, days: float
) -> dict[str, Any]:
    m = resolved_market(base, mid, outcome, days)
    m.update(createdTime=ms(created), resolutionTime=ms(created + timedelta(days=days)))
    return m


class SearchApi:
    """A scripted /v0/search-markets that pages by ``beforeTime`` (newest first)."""

    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self.markets = sorted(markets, key=lambda m: -m["createdTime"])
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET" and request.url.path == "/v0/search-markets"
        q = dict(request.url.params)
        assert q["filter"] == "resolved" and q["sort"] == "newest" and q["contractType"] == "BINARY"
        rows = [m for m in self.markets if m["createdTime"] < int(q["beforeTime"])]
        return httpx.Response(200, json=rows[: int(q["limit"])])

    def client(self) -> ManifoldClient:
        return ManifoldClient(
            BASE, http=httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        )


@pytest.fixture
def empty_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'u.db'}")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _base_market() -> dict[str, Any]:
    return next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )


def test_the_universe_is_sampled_across_time_and_keeps_only_yes_no_resolutions(
    empty_factory: sessionmaker[Session],
) -> None:
    from ambre.data_engine.history_fetch import load_resolved_universe
    from ambre.infrastructure.orm import MarketRow

    base = _base_market()
    markets = []
    for k in range(6):  # one market every 100 days
        created = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=100 * k)
        markets.append(lite(base, f"synthetic_u{k}", "YES" if k % 2 else "NO", created, 10))
    markets.append(lite(base, "synthetic_cancel", "CANCEL", datetime(2025, 2, 1, tzinfo=UTC), 5))
    api = SearchApi(markets)
    report = asyncio.run(
        load_resolved_universe(
            api.client(),
            empty_factory,
            lambda: datetime(2026, 11, 2, tzinfo=UTC),
            since=datetime(2025, 1, 1, tzinfo=UTC),
            stride_days=100,
            per_anchor=1,
        )
    )
    assert report.anchors == 7 and report.listed == 7
    assert report.stored >= 4 and report.skipped_resolution == 1  # the CANCEL one is left out
    assert report.stored + report.skipped_resolution <= report.listed  # no market counted twice
    assert all(r.method == "GET" for r in api.requests)
    with empty_factory() as s:
        assert repo.historical_markets(s) == []  # no history yet: it is fetched separately
        ids = sorted(repo.markets_needing_history(s, 50))
        assert len(ids) == report.stored and "synthetic_cancel" not in ids
        created = [s.get(MarketRow, i).created_at for i in ids]  # type: ignore[union-attr]
    assert (max(created) - min(created)).days > 300  # spread over time, not piled on one period
    assert count(empty_factory, DecisionRow) == 0


def test_loading_the_universe_twice_stores_nothing_new_and_adds_no_snapshot(
    empty_factory: sessionmaker[Session],
) -> None:
    from ambre.data_engine.history_fetch import load_resolved_universe
    from ambre.infrastructure.orm import MarketRow, SnapshotRow

    base = _base_market()
    api = SearchApi([lite(base, "synthetic_u0", "YES", datetime(2025, 5, 1, tzinfo=UTC), 7)])

    def run() -> Any:
        return asyncio.run(
            load_resolved_universe(
                api.client(),
                empty_factory,
                lambda: datetime(2026, 11, 2, tzinfo=UTC),
                since=datetime(2025, 1, 1, tzinfo=UTC),
                stride_days=200,
                per_anchor=5,
            )
        )

    run()
    run()
    assert count(empty_factory, MarketRow) == 1
    assert count(empty_factory, SnapshotRow) == 0  # a resolved market has no live snapshot

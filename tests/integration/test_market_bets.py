"""Bet-level history of resolved markets (ADR-022): fetching and storing, offline."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from builders import T_SYNC, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_history_acquisition import CREATED, Api, bets_for, ms, resolved_market

from ambre.data_engine.history_fetch import fetch_market_bets
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import (
    Base,
    MarketBetRow,
    MarketBetStateRow,
    PricePointRow,
)

NOW = T_SYNC + timedelta(days=1)


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'b.db'}")
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
            resolved_market(base, "synthetic_a", "YES", 20),
            resolved_market(base, "synthetic_b", "NO", 10),
        ],
        T_SYNC,
    )
    yield f
    engine.dispose()


def api() -> Api:
    a: dict[str, list[dict[str, Any]]] = {
        "synthetic_a": [
            b | {"probBefore": b["probBefore"] / 4, "probAfter": b["probAfter"] / 4}
            for b in bets_for("synthetic_a", 1500)  # beyond 500 bets its prices leave [0, 1]
        ],
        "synthetic_b": bets_for("synthetic_b", 3),
    }
    partly_filled = {
        "id": "synthetic_b-cancelled",
        "contractId": "synthetic_b",
        "createdTime": ms(CREATED + timedelta(days=1)),
        "probBefore": 0.503,
        "probAfter": 0.9,
        "isCancelled": True,
        "isRedemption": False,
        "amount": 40.0,
        "userId": "synthetic_user",
        "fills": [{"amount": 40.0, "timestamp": ms(CREATED + timedelta(days=1))}],
    }
    a["synthetic_b"] = [partly_filled, *a["synthetic_b"]]
    return Api(a)


def fetch(a: Api, f: sessionmaker[Session], limit: int = 10, max_pages: int = 30) -> Any:
    run = fetch_market_bets(a.client(), f, lambda: NOW, limit=limit, max_pages=max_pages)
    return asyncio.run(run)


def count(f: sessionmaker[Session], model: type) -> int:
    with f() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def test_every_market_is_read_once_and_only_with_get(factory: sessionmaker[Session]) -> None:
    a = api()
    report = fetch(a, factory)
    assert (report.requested, report.complete, report.incomplete) == (2, 2, 0)
    assert count(factory, MarketBetRow) == 1504
    assert all(r.method == "GET" for r in a.requests)
    before = len(a.requests)
    assert fetch(a, factory).requested == 0  # nothing left to read
    assert len(a.requests) == before


def test_a_rerun_resumes_after_an_interruption(factory: sessionmaker[Session]) -> None:
    a = api()
    assert fetch(a, factory, limit=1).requested == 1
    assert fetch(a, factory).requested == 1
    assert count(factory, MarketBetStateRow) == 2


def test_a_failure_stores_nothing_and_the_market_stays_to_read(
    factory: sessionmaker[Session],
) -> None:
    a = api()
    a.fail.add("synthetic_a")
    report = fetch(a, factory)
    assert [x["market_id"] for x in report.failed] == ["synthetic_a"]
    with factory() as s:
        assert repo.markets_needing_bets(s, 10) == ["synthetic_a"]


def test_a_truncated_read_is_stored_as_incomplete(factory: sessionmaker[Session]) -> None:
    report = fetch(api(), factory, max_pages=1)
    assert report.incomplete == 1
    with factory() as s:
        assert s.get(MarketBetStateRow, "synthetic_a").complete is False  # type: ignore[union-attr]


def test_the_earlier_price_points_are_left_as_they_were(factory: sessionmaker[Session]) -> None:
    """ADR-018 stays reproducible: the corrected history lives in its own tables."""
    with factory() as s:
        s.add(PricePointRow(market_id="synthetic_a", bet_id="old", at=CREATED, probability=0.5))
        s.commit()
    fetch(api(), factory)
    assert count(factory, PricePointRow) == 1


def test_stored_bets_give_the_price_of_a_partly_filled_cancelled_order(
    factory: sessionmaker[Session],
) -> None:
    fetch(api(), factory)
    with factory() as s:
        markets = {m.market_id: m for m in repo.bet_level_markets(s)}
    m = markets["synthetic_b"]
    assert m.complete and m.outcome is False
    last = max(m.events, key=lambda e: e.at)
    assert (last.bet_id, last.prob_after, last.amount) == ("synthetic_b-cancelled", 0.9, 40.0)
    assert last.bettor is not None and "synthetic_user" not in last.bettor
    assert m.bet_count == 4

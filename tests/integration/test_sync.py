import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.data_engine.sync import MarketSyncService
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, IgnoredMarketRow, MarketRow, SnapshotRow
from ambre.infrastructure.repositories import get_sync_state
from ambre.integrations.manifold.client import ManifoldError, ManifoldUnavailable

FIXTURES = Path(__file__).parent.parent / "fixtures" / "manifold"
T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


def lut(m: dict[str, Any]) -> int:
    return int(m["lastUpdatedTime"])


class FakeClient:
    """Serves a list of markets in updated-time desc order with `before` cursors."""

    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self.markets = markets
        self.calls: list[tuple[int, str | None]] = []
        self.fail_on_call: int | None = None
        self.fail_with: Exception = ManifoldUnavailable("GET /v0/markets failed after 6 attempts")

    async def list_markets(self, *, limit: int, before: str | None = None) -> list[dict[str, Any]]:
        self.calls.append((limit, before))
        if self.fail_on_call == len(self.calls):
            raise self.fail_with
        ordered = sorted(self.markets, key=lambda m: m.get("lastUpdatedTime") or 0, reverse=True)
        start = 0
        if before is not None:
            start = [m["id"] for m in ordered].index(before) + 1
        return copy.deepcopy(ordered[start : start + limit])


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def make(factory: sessionmaker[Session], client: FakeClient) -> MarketSyncService:
    return MarketSyncService(client, factory, Clock())  # type: ignore[arg-type]


def count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


async def test_first_sync_stores_binary_ignores_others(factory: sessionmaker[Session]) -> None:
    client = FakeClient(load("markets_updated_page.json"))
    report = await make(factory, client).sync(max_pages=5, page_size=1000)
    assert report.completed is True
    assert report.error is None
    assert report.pages == 1
    assert report.stored_markets == 21
    assert report.new_snapshots == 21
    assert report.duplicate_snapshots == 0
    assert report.ignored == 32
    assert report.invalid == 0
    assert count(factory, MarketRow) == 21
    assert count(factory, SnapshotRow) == 21
    assert count(factory, IgnoredMarketRow) == 32
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms == max(lut(m) for m in client.markets)
        assert state.last_success_at is not None
        assert state.last_error is None
        snap = s.scalars(select(SnapshotRow)).first()
        assert snap is not None
        assert snap.source_id == "manifold:/v0/markets"
        assert snap.ingested_at > snap.observed_at


async def test_second_sync_stops_at_watermark_and_dedups(factory: sessionmaker[Session]) -> None:
    markets = load("markets_updated_page.json")
    client = FakeClient(markets)
    await make(factory, client).sync(max_pages=5, page_size=20)
    first_calls = len(client.calls)
    assert first_calls == 3  # 53 markets, pages of 20

    client.calls.clear()
    report = await make(factory, client).sync(max_pages=5, page_size=20)
    # Newest page already contains markets at or below the watermark: stop after it.
    assert len(client.calls) == 1
    assert report.pages == 1
    assert report.completed is True
    assert report.new_snapshots == 0
    assert report.duplicate_snapshots > 0
    assert count(factory, SnapshotRow) == 21


async def test_second_sync_picks_up_new_update(factory: sessionmaker[Session]) -> None:
    markets = load("markets_updated_page.json")
    client = FakeClient(markets)
    await make(factory, client).sync(max_pages=5, page_size=20)
    binary = next(m for m in markets if m["outcomeType"] == "BINARY")
    binary["lastUpdatedTime"] = max(lut(m) for m in markets) + 5000
    binary["probability"] = 0.123
    report = await make(factory, client).sync(max_pages=5, page_size=20)
    assert report.new_snapshots == 1
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None and state.high_watermark_ms == binary["lastUpdatedTime"]
    assert count(factory, SnapshotRow) == 22


async def test_failure_on_page_3_keeps_pages_1_2_and_watermark(
    factory: sessionmaker[Session],
) -> None:
    markets = load("markets_updated_page.json")
    client = FakeClient(markets)
    client.fail_on_call = 3
    report = await make(factory, client).sync(max_pages=5, page_size=20)
    assert report.completed is False
    assert report.pages == 2
    assert report.error is not None and "failed after" in report.error
    assert count(factory, MarketRow) + count(factory, IgnoredMarketRow) == 40
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms is None
        assert state.last_success_at is None
        assert state.last_error is not None


async def test_resume_after_failure_completes(factory: sessionmaker[Session]) -> None:
    client = FakeClient(load("markets_updated_page.json"))
    client.fail_on_call = 3
    await make(factory, client).sync(max_pages=5, page_size=20)
    client.fail_on_call = None
    report = await make(factory, client).sync(max_pages=5, page_size=20)
    assert report.completed is True
    assert report.error is None
    assert count(factory, MarketRow) == 21
    assert count(factory, IgnoredMarketRow) == 32
    assert count(factory, SnapshotRow) == 21
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms == max(lut(m) for m in client.markets)
        assert state.last_error is None


async def test_failure_keeps_previous_watermark(factory: sessionmaker[Session]) -> None:
    markets = load("markets_updated_page.json")
    client = FakeClient(markets)
    await make(factory, client).sync(max_pages=5, page_size=20)
    with factory() as s:
        before = get_sync_state(s, "markets")
        assert before is not None
        mark, success = before.high_watermark_ms, before.last_success_at
    client.fail_on_call = len(client.calls) + 1
    report = await make(factory, client).sync()
    assert report.completed is False
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms == mark
        assert state.last_success_at == success
        assert state.last_error is not None


async def test_non_retryable_error_reported_not_raised(factory: sessionmaker[Session]) -> None:
    client = FakeClient(load("markets_updated_page.json"))
    client.fail_on_call = 1
    client.fail_with = ManifoldError("HTTP 400 for GET /v0/markets")
    report = await make(factory, client).sync()
    assert report.completed is False
    assert report.error == "HTTP 400 for GET /v0/markets"


async def test_invalid_market_counted_not_fatal(factory: sessionmaker[Session]) -> None:
    markets = load("markets_updated_page.json")
    binary = [m for m in markets if m["outcomeType"] == "BINARY"]
    del binary[0]["pool"]
    del binary[1]["probability"]
    client = FakeClient(markets)
    report = await make(factory, client).sync()
    assert report.completed is True
    assert report.invalid == 2
    assert report.stored_markets == 19
    assert report.ignored == 32
    with factory() as s:
        invalid_rows = s.scalars(
            select(IgnoredMarketRow).where(IgnoredMarketRow.reason.like("invalid: %"))
        ).all()
    assert {r.market_id for r in invalid_rows} == {binary[0]["id"], binary[1]["id"]}
    assert count(factory, IgnoredMarketRow) == 34


async def test_snapshot_without_event_at_dedup_by_values(factory: sessionmaker[Session]) -> None:
    market = next(m for m in load("synthetic_edge_cases.json") if m["id"] == "synth_no_optional")
    assert market.get("lastUpdatedTime") is None
    client = FakeClient([market])
    svc = make(factory, client)
    assert (await svc.sync()).new_snapshots == 1
    again = await svc.sync()
    assert again.new_snapshots == 0 and again.duplicate_snapshots == 1
    market["probability"] = 0.66
    changed = await svc.sync()
    assert changed.new_snapshots == 1
    assert count(factory, SnapshotRow) == 2
    market["pool"] = {"YES": market["pool"]["YES"] + 1.0, "NO": market["pool"]["NO"]}
    assert (await svc.sync()).new_snapshots == 1
    assert count(factory, SnapshotRow) == 3
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None and state.high_watermark_ms is None


async def test_max_pages_truncation_does_not_advance_existing_watermark(
    factory: sessionmaker[Session],
) -> None:
    markets = load("markets_updated_page.json")
    client = FakeClient(markets)
    await make(factory, client).sync(max_pages=5, page_size=20)
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        mark = state.high_watermark_ms
    # Every market is newer than the stored watermark: nothing to stop on.
    for m in markets:
        m["lastUpdatedTime"] = lut(m) + 10_000_000
    report = await make(factory, client).sync(max_pages=1, page_size=20)
    assert report.completed is False
    assert report.error is not None and "max_pages" in report.error
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None and state.high_watermark_ms == mark


def _backlog(n: int, base_ms: int, prefix: str = "backlog") -> list[dict[str, Any]]:
    template = next(m for m in load("markets_updated_page.json") if m["outcomeType"] == "BINARY")
    out = []
    for i in range(n):
        m = copy.deepcopy(template)
        m["id"] = f"{prefix}{i:03d}"
        m["lastUpdatedTime"] = base_ms + i
        out.append(m)
    return out


async def test_catch_up_converges_over_runs(factory: sessionmaker[Session]) -> None:
    old = _backlog(5, 1_000, "old")
    client = FakeClient(old)
    await make(factory, client).sync(max_pages=5, page_size=10)
    w = 1_004
    backlog = _backlog(25, 10_000)
    client.markets = old + backlog
    svc = make(factory, client)

    first = await svc.sync(max_pages=1, page_size=10)
    assert first.completed is False and "catch-up" in (first.error or "")
    second = await svc.sync(max_pages=1, page_size=10)
    assert second.completed is False
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms == w
        assert state.resume_cursor is not None
        assert state.pending_watermark_ms == 10_024
    third = await svc.sync(max_pages=1, page_size=10)
    assert third.completed is True and third.error is None
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms == 10_024
        assert state.resume_cursor is None and state.pending_watermark_ms is None
        assert state.last_success_at is not None and state.last_error is None
    assert count(factory, MarketRow) == 30


async def test_update_during_catch_up_picked_up_by_next_fresh_run(
    factory: sessionmaker[Session],
) -> None:
    old = _backlog(5, 1_000, "old")
    client = FakeClient(old)
    await make(factory, client).sync(max_pages=5, page_size=10)
    backlog = _backlog(25, 10_000)
    client.markets = old + backlog
    svc = make(factory, client)
    await svc.sync(max_pages=1, page_size=10)
    # A market on the already-read pages is updated mid catch-up.
    backlog[-1]["lastUpdatedTime"] = 50_000
    backlog[-1]["probability"] = 0.31
    for _ in range(2):
        await svc.sync(max_pages=1, page_size=10)
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None and state.high_watermark_ms == 10_024
    fresh = await svc.sync(max_pages=1, page_size=10)
    assert fresh.completed is True
    assert fresh.new_snapshots == 1
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None and state.high_watermark_ms == 50_000


async def test_failure_mid_catch_up_keeps_committed_cursor(
    factory: sessionmaker[Session],
) -> None:
    old = _backlog(5, 1_000, "old")
    client = FakeClient(old)
    await make(factory, client).sync(max_pages=5, page_size=10)
    client.markets = old + _backlog(25, 10_000)
    client.calls.clear()
    client.fail_on_call = 2
    report = await make(factory, client).sync(max_pages=3, page_size=10)
    assert report.completed is False and report.pages == 1
    with factory() as s:
        state = get_sync_state(s, "markets")
        assert state is not None
        assert state.high_watermark_ms == 1_004
        assert state.resume_cursor is not None

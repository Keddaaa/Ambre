"""Shared news ingestion end to end, offline: poll once, store, cluster, serve many markets.

Everything runs on a mocked transport and a controlled clock; hand-written fixtures only."""

import asyncio
import copy
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from builders import SETTINGS, T_SYNC, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, MarketRow, PositionRow
from ambre.intelligence import store
from ambre.intelligence.connectors import build_providers
from ambre.intelligence.news_ingest import NewsIngestReport, ingest_news, news_metrics
from ambre.intelligence.orm import (
    IntelFeatureJournalRow,
    IntelFeedStateRow,
    IntelMarketLinkRow,
    IntelObservationRow,
    IntelRequestRow,
)
from ambre.intelligence.planning import load_gazetteer
from ambre.intelligence.registry import Registry, parse_registry

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "news"
CONFIGS = Path(__file__).resolve().parents[2] / "configs"
NOW = T_SYNC + timedelta(hours=40)  # after the fixture articles (2026-11-01) were published
SNAPI_HOST = "api.spaceflightnewsapi.net"
FEED_HOST = "feeds.example"

STARSHIP_A = "Will SpaceX Starship complete an orbital refueling demonstration before 2027?"
STARSHIP_B = "Will SpaceX Starship complete orbital refueling in 2026?"
GLENN = "Will Blue Origin launch New Glenn again before 2027?"
LAKERS = "Will the Lakers win the NBA championship in 2027?"


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def market(base: dict[str, Any], mid: str, question: str) -> dict[str, Any]:
    m = copy.deepcopy(base)
    m.update(
        id=mid, question=question, slug=mid, url=f"https://example.invalid/{mid}", probability=0.4
    )
    m["lastUpdatedTime"] = ms(T_SYNC)
    m["closeTime"] = ms(datetime(2027, 4, 1, tzinfo=UTC))
    return m


def registry(
    *, snapi: bool = True, feed: bool = False, snapi_budget: dict | None = None
) -> Registry:  # type: ignore[type-arg]
    doc = yaml.safe_load((CONFIGS / "providers.yaml").read_text(encoding="utf-8"))
    doc["providers"]["spaceflight_news"]["enabled"] = snapi
    if snapi_budget is not None:
        doc["providers"]["spaceflight_news"]["internal_budget"] = snapi_budget
    doc["providers"]["synthetic_feed"] = {
        "name": "Synthetic World News",
        "domain": "news",
        "status": "VERIFIED_FREE",
        "enabled": feed,
        "authentication": "none",
        "verified_on": "2026-11-02",
        "connector": "rss_feed",
        "endpoint": f"https://{FEED_HOST}/world.xml",
        "internal_budget": {"min_interval_seconds": 60, "per_hour": 30, "per_day": 300},
        "freshness_seconds": 600,
        "capabilities": {"news_feed": 0.4},
    }
    return parse_registry(doc)


class Net:
    def __init__(self) -> None:
        self.snapi: list[httpx.Request] = []
        self.feed: list[httpx.Request] = []
        self.snapi_handler: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json=json.loads((FIX / "synthetic_snapi_page.json").read_text(encoding="utf-8"))
        )
        self.feed_handler: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, content=(FIX / "synthetic_rss.xml").read_bytes(), headers={"ETag": '"v1"'}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == SNAPI_HOST:
            self.snapi.append(request)
            return self.snapi_handler(request)
        if request.url.host == FEED_HOST:
            self.feed.append(request)
            return self.feed_handler(request)
        raise AssertionError(f"unexpected host {request.url.host}")  # nothing else is reachable


class Harness:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.net = Net()
        self.gazetteer = load_gazetteer(CONFIGS / "gazetteer.yaml")

    def run(self, now: datetime = NOW, reg: Registry | None = None) -> NewsIngestReport:
        reg = reg or registry()
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.net.handle))
        providers = build_providers(reg, http, lambda: now)
        with self.factory() as s:
            report = asyncio.run(
                ingest_news(
                    s, SETTINGS, now, registry=reg, providers=providers, gazetteer=self.gazetteer
                )
            )
            s.commit()
        return report


def make_harness(tmp_path: Path, questions: dict[str, str]) -> Harness:
    engine = make_engine(f"sqlite:///{tmp_path / 'n.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    sync(factory, [market(base, mid, q) for mid, q in questions.items()], T_SYNC)
    return Harness(factory)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield make_harness(
        tmp_path,
        {
            "synthetic_sa": STARSHIP_A,
            "synthetic_sb": STARSHIP_B,
            "synthetic_ng": GLENN,
            "synthetic_lk": LAKERS,
        },
    )


def count(h: Harness, model: type) -> int:
    with h.factory() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def links_by_market(h: Harness) -> dict[str, set[str]]:
    with h.factory() as s:
        out: dict[str, set[str]] = {}
        for market_id, obs_id in s.execute(
            select(IntelMarketLinkRow.market_id, IntelObservationRow.title).join(
                IntelObservationRow,
                IntelObservationRow.observation_id == IntelMarketLinkRow.observation_id,
            )
        ):
            out.setdefault(market_id, set()).add(obs_id)
        return out


# ------------------------------------------------------------------ one poll, many markets


def test_one_request_serves_every_market_and_a_story_reaches_several_of_them(h: Harness) -> None:
    report = h.run()
    assert len(h.net.snapi) == 1  # a single request, whatever the number of markets
    assert report.polls[0]["provider"] == "spaceflight_news" and report.polls[0]["status"] == "OK"
    assert report.new_observations == 4 and report.polls[0]["invalid"] == 1
    links = links_by_market(h)
    refuel = "SpaceX Starship completes first orbital refueling test"
    assert (
        refuel in links["synthetic_sa"] and refuel in links["synthetic_sb"]
    )  # one story, two markets
    assert any("Blue Origin New Glenn" in t for t in links["synthetic_ng"])
    assert "synthetic_lk" not in links  # nothing about the Lakers: no link, not a weak one
    assert report.new_links == sum(len(v) for v in links.values())


def test_links_are_context_never_a_prediction_and_decisions_stay_untouched(h: Harness) -> None:
    h.run()
    with h.factory() as s:
        rows = s.scalars(select(IntelMarketLinkRow)).all()
        assert rows and all(
            r.relation == "news_mention" and r.usable_for_prediction is False for r in rows
        )
        assert any("resolution criteria not read" in " ".join(r.reasons) for r in rows)
    for model in (DecisionRow, PositionRow, LedgerRow):
        assert count(h, model) == 0


def test_the_four_clocks_are_stored_apart(h: Harness) -> None:
    h.run()
    with h.factory() as s:
        row = s.scalars(
            select(IntelObservationRow).where(IntelObservationRow.title.like("SpaceX Starship%"))
        ).first()
        assert row is not None
        assert row.published_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
        assert row.available_at == NOW and row.ingested_at == NOW  # read at the poll, not before
        assert row.event_at is None and row.verification_status == "unverified"


def test_features_are_journaled_dated_and_count_events_not_just_copies(h: Harness) -> None:
    report = h.run()
    assert report.journaled >= 2
    with h.factory() as s:
        rows = s.scalars(
            select(IntelFeatureJournalRow).where(IntelFeatureJournalRow.market_id == "synthetic_sa")
        ).all()
        assert len(rows) == 1
        names = {f["name"]: f for f in rows[0].features}
        assert names["news.article_count"]["value"] == 2.0  # both reports of the refueling
        assert names["news.distinct_events"]["value"] == 1.0  # ... which are one event
        assert all(f["available_at"] <= NOW.isoformat() for f in rows[0].features)
        assert rows[0].market_probability == pytest.approx(0.4)


# ------------------------------------------------------------------ cache, idempotence


def test_a_second_ingestion_inside_the_freshness_window_asks_nothing(h: Harness) -> None:
    h.run()
    again = h.run(NOW + timedelta(minutes=5))
    assert len(h.net.snapi) == 1
    assert again.polls[0]["status"] == "SKIPPED_FRESH" and again.new_observations == 0
    assert again.new_links == 0 and count(h, IntelRequestRow) == 1


def test_polling_again_later_never_duplicates_articles_links_or_journal_rows(h: Harness) -> None:
    first = h.run()
    articles, links, journal = (
        count(h, IntelObservationRow),
        count(h, IntelMarketLinkRow),
        count(h, IntelFeatureJournalRow),
    )
    later = h.run(NOW + timedelta(hours=2))
    assert len(h.net.snapi) == 2 and later.polls[0]["status"] == "OK"
    assert later.new_observations == 0 and later.new_links == 0 and later.journaled == 0
    assert (
        count(h, IntelObservationRow),
        count(h, IntelMarketLinkRow),
        count(h, IntelFeatureJournalRow),
    ) == (articles, links, journal)
    assert first.new_links > 0
    # the second request only asks for what is newer than the newest article, with an overlap
    since = dict(h.net.snapi[1].url.params)["published_at_gte"]
    assert since.startswith("2026-10-31T18:00:00")  # 2026-11-01T18:00Z minus 24 h


# ------------------------------------------------------------------ failure isolation


def test_a_failing_source_never_blocks_the_others_and_is_paused_with_backoff(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path, {"synthetic_sa": STARSHIP_A})
    h.net.snapi_handler = lambda r: httpx.Response(503)
    h.net.feed_handler = lambda r: httpx.Response(
        200,
        content=b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><item>
        <title>SpaceX Starship completes orbital refueling test again</title>
        <link>https://world.example/space/refuel</link><guid>g1</guid>
        <pubDate>Sun, 01 Nov 2026 20:00:00 GMT</pubDate></item></channel></rss>""",
        headers={"ETag": '"f1"'},
    )
    reg = registry(feed=True)
    report = h.run(NOW, reg)
    by = {p["provider"]: p for p in report.polls}
    assert (
        by["spaceflight_news"]["status"] == "FAILED" and by["spaceflight_news"]["pause_seconds"] > 0
    )
    assert by["synthetic_feed"]["status"] == "OK" and by["synthetic_feed"]["new"] == 1
    assert "synthetic_sa" in links_by_market(h)  # the healthy feed still served the market
    with h.factory() as s:
        state = store.get_state(s, "spaceflight_news")
        assert state is not None and state.consecutive_failures == 1 and state.paused_until
    # the next attempt, inside the pause, is refused by the budget controller: no request made
    n = len(h.net.snapi)
    later = h.run(NOW + timedelta(seconds=30), reg)
    snapi = next(p for p in later.polls if p["provider"] == "spaceflight_news")
    assert snapi["status"] == "SKIPPED_BUDGET" and "paused" in snapi["detail"]
    assert len(h.net.snapi) == n


def test_a_connector_that_raises_is_that_feeds_failure_not_a_crash(tmp_path: Path) -> None:
    h = make_harness(tmp_path, {"synthetic_sa": STARSHIP_A})

    def explode(_: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected bug")

    h.net.snapi_handler = explode
    report = h.run(NOW, registry(feed=True))
    by = {p["provider"]: p for p in report.polls}
    assert by["spaceflight_news"]["status"] == "FAILED"  # transport-level, classified
    assert by["synthetic_feed"]["status"] == "OK"


def test_quota_exhaustion_defers_the_poll_without_a_request(h: Harness) -> None:
    reg = registry(snapi_budget={"per_hour": 2, "min_interval_seconds": 60})
    h.run(NOW, reg)
    later = h.run(NOW + timedelta(minutes=20), reg)  # fresh window passed, hourly quota used
    assert len(h.net.snapi) == 1
    assert later.polls[0]["status"] == "SKIPPED_BUDGET"
    recovered = h.run(NOW + timedelta(hours=2), reg)
    assert recovered.polls[0]["status"] == "OK" and len(h.net.snapi) == 2


def test_no_enabled_news_source_means_no_request_and_no_error(h: Harness) -> None:
    report = h.run(NOW, registry(snapi=False))
    assert report.polls == [] and not h.net.snapi and report.new_links == 0


# ------------------------------------------------------------------ conditional requests


def test_an_unchanged_feed_is_asked_with_its_validators_and_answers_304(tmp_path: Path) -> None:
    h = make_harness(tmp_path, {"synthetic_sa": STARSHIP_A})
    reg = registry(snapi=False, feed=True)
    h.run(NOW, reg)
    with h.factory() as s:
        state = s.get(IntelFeedStateRow, "synthetic_feed")
        assert state is not None and state.etag == '"v1"' and state.last_status == "OK"
    h.net.feed_handler = lambda r: httpx.Response(304)
    later = h.run(NOW + timedelta(hours=1), reg)
    assert h.net.feed[1].headers["If-None-Match"] == '"v1"'
    poll = later.polls[0]
    assert poll["status"] == "EMPTY" and poll["new"] == 0 and poll["http_status"] == 304
    with h.factory() as s:
        state = s.get(IntelFeedStateRow, "synthetic_feed")
        assert state is not None and state.etag == '"v1"'  # kept through the 304


def test_a_failed_poll_does_not_erase_the_validators(tmp_path: Path) -> None:
    h = make_harness(tmp_path, {"synthetic_sa": STARSHIP_A})
    reg = registry(snapi=False, feed=True)
    h.run(NOW, reg)
    h.net.feed_handler = lambda r: httpx.Response(500)
    h.run(NOW + timedelta(hours=1), reg)
    with h.factory() as s:
        state = s.get(IntelFeedStateRow, "synthetic_feed")
        assert state is not None and state.last_status == "FAILED" and state.etag == '"v1"'


# ------------------------------------------------------------------ measurement


def test_metrics_count_articles_events_and_markets_from_stored_rows_only(h: Harness) -> None:
    h.run()
    with h.factory() as s:
        m = news_metrics(s, registry(), NOW)
    assert m["articles_by_provider"] == {"spaceflight_news": 4}
    assert m["articles_in_window"] == 4
    assert m["unique_events_in_window"] == 3  # refueling (2 reports), New Glenn, Artemis
    assert m["events_with_several_sources"] == 1
    assert m["markets_with_news"] == 3 and m["articles_serving_several_markets"] >= 1
    assert m["links_usable_for_prediction"] == 0
    assert m["feature_rows_with_news"] >= 3


def test_metrics_with_nothing_ingested_are_zero_or_none_never_invented(h: Harness) -> None:
    with h.factory() as s:
        m = news_metrics(s, registry(), NOW)
    assert m["articles_in_window"] == 0 and m["unique_events_in_window"] == 0
    assert m["articles_per_event"] is None and m["markets_with_news"] == 0


def test_the_ingestion_module_never_imports_execution_portfolio_or_risk() -> None:
    src = (Path(__file__).resolve().parents[2] / "src/ambre/intelligence/news_ingest.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("execution_engine", "ambre.portfolio", "risk_engine", "survival_engine"):
        assert forbidden not in src


def test_markets_table_is_read_only_here(h: Harness) -> None:
    with h.factory() as s:
        before = [(m.id, m.is_resolved, m.resolution) for m in s.scalars(select(MarketRow))]
    h.run()
    with h.factory() as s:
        assert before == [(m.id, m.is_resolved, m.resolution) for m in s.scalars(select(MarketRow))]


def test_articles_read_during_the_poll_are_not_rejected_as_coming_from_the_future(
    h: Harness,
) -> None:
    """The provider stamps an article when the answer arrives, which is after the instant the
    ingestion started. The association step must look at the world after the poll, not before
    it, or every freshly fetched article would be 'available after the decision'."""
    reg = registry()
    started = NOW
    http = httpx.AsyncClient(transport=httpx.MockTransport(h.net.handle))
    providers = build_providers(reg, http, lambda: started + timedelta(seconds=3))  # read later
    with h.factory() as s:
        report = asyncio.run(
            ingest_news(
                s,
                SETTINGS,
                started,
                registry=reg,
                providers=providers,
                gazetteer=h.gazetteer,
                clock=lambda: started + timedelta(seconds=5),
            )
        )
        s.commit()
    assert report.new_observations == 4 and report.new_links > 0
    with h.factory() as s:
        rows = s.scalars(select(IntelFeatureJournalRow)).all()
        assert rows and all(
            f["available_at"] <= r.as_of.isoformat() for r in rows for f in r.features
        )

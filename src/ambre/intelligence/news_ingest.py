"""Shared news ingestion: read every authorised feed once, then serve every market.

The per-market search of ``service.research_pass`` asks a search engine one question per topic.
Feeds and streams are the opposite shape: the same list of recent articles is useful to many
markets, so each source is polled once, its items stored as unverified observations, and the
*stored* articles are matched to every open market locally, with no further request.

* One failing, paused or exhausted source never stops the others (each poll is isolated), and a
  failure of this job never touches the research pass (they are separate scheduled jobs).
* Polling reuses the existing machinery: the Budget Controller, provider pauses and backoff,
  the request journal, and a freshness window so an unchanged feed is not asked again.
* An article is linked to a market as a ``news_mention`` that is never usable for prediction on
  its own: a headline is a report, and the resolution criteria are not read.
* Nothing here reads or writes decisions, positions or the ledger, and nothing produces a
  probability.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ambre.config.settings import Settings
from ambre.infrastructure import repositories as repo
from ambre.intelligence import store
from ambre.intelligence.budget import backoff_seconds
from ambre.intelligence.generic_planning import MarketPlan, plan_market
from ambre.intelligence.matching import match_news
from ambre.intelligence.models import (
    FetchResult,
    InfoNeed,
    NewsArticlePayload,
    Observation,
    ProviderCard,
    ResultKind,
)
from ambre.intelligence.news_events import NewsItem, cluster_items
from ambre.intelligence.orm import (
    IntelFeatureJournalRow,
    IntelMarketLinkRow,
    IntelObservationRow,
    IntelRequestRow,
)
from ambre.intelligence.planning import Gazetteer
from ambre.intelligence.providers.base import Provider
from ambre.intelligence.registry import Registry
from ambre.intelligence.resolution import ResearchRules, analyze_market, load_rules
from ambre.intelligence.service import _budget_for, _journal_features
from ambre.pipeline import to_market

logger = logging.getLogger(__name__)

NEED = InfoNeed.NEWS_FEED
#: How far back a first poll asks, and how long stored articles stay candidates for a market.
DEFAULT_WINDOW_DAYS = 7
#: A poll asks for items newer than the newest one stored, minus this overlap for late additions.
POLL_OVERLAP = timedelta(hours=24)
SKIPPED_FRESH = "SKIPPED_FRESH"
SKIPPED_BUDGET = "SKIPPED_BUDGET"
SKIPPED_NO_CONNECTOR = "SKIPPED_NO_CONNECTOR"


@dataclass
class NewsIngestReport:
    #: one entry per feed provider, whatever happened to it
    polls: list[dict[str, Any]] = field(default_factory=list)
    new_observations: int = 0
    new_links: int = 0
    journaled: int = 0
    articles_in_window: int = 0
    candidate_markets: int = 0
    markets_newly_linked: list[str] = field(default_factory=list)


def _poll_kwargs(
    session: Session, card: ProviderCard, now: datetime, window: timedelta
) -> dict[str, Any]:
    state = store.get_feed_state(session, card.id)
    since = (
        state.newest_published_at - POLL_OVERLAP
        if state is not None and state.newest_published_at is not None
        else now - window
    )
    return {
        "etag": state.etag if state else None,
        "last_modified": state.last_modified if state else None,
        "since": since,
    }


async def _poll_one(
    session: Session,
    settings: Settings,
    card: ProviderCard,
    provider: Provider | None,
    now: datetime,
    window: timedelta,
) -> dict[str, Any]:
    """Poll one feed. Never raises: whatever goes wrong is this feed's result, nobody else's."""
    entry: dict[str, Any] = {
        "provider": card.id,
        "status": "",
        "items": 0,
        "new": 0,
        "detail": None,
    }
    try:
        state = store.get_feed_state(session, card.id)
        if (
            card.freshness_seconds is not None
            and state is not None
            and state.last_polled_at is not None
            and state.last_status in ("OK", "EMPTY")
            and (now - state.last_polled_at).total_seconds() < card.freshness_seconds
        ):
            entry.update(status=SKIPPED_FRESH, detail="polled less than the freshness window ago")
            return entry
        health = store.get_state(session, card.id)
        budget = _budget_for(session, card, now, health.paused_until if health else None)
        if not budget.allowed:
            entry.update(status=SKIPPED_BUDGET, detail=budget.reason)
            return entry
        if provider is None:
            entry.update(status=SKIPPED_NO_CONNECTOR, detail=f"no connector for {card.id}")
            return entry

        request_id = str(uuid.uuid4())
        store.record_usage(session, card.id, now, purpose="news_ingest", request_id=request_id)
        kwargs = _poll_kwargs(session, card, now, window)
        try:
            result = await provider.fetch_latest(**kwargs)
        except Exception as exc:  # a connector bug is this feed's failure, not a crash
            logger.exception("feed %s raised", card.id)
            result = FetchResult(kind=ResultKind.FAILED, detail=f"{type(exc).__name__}: {exc}")

        status = result.kind.value.upper()
        session.add(
            IntelRequestRow(
                id=request_id,
                created_at=now,
                need=NEED.value,
                status=status,
                provider_id=card.id,
                market_ids=[],
                params={
                    k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in kwargs.items()
                },
                value_score=None,
                cost_units=1,
                observation_count=len(result.observations),
                invalid_items=result.invalid_items,
                detail=result.detail,
                explanation={
                    "why": "shared news ingestion: one poll serves every market",
                    "http_status": result.http_status,
                    "cost": {"provider": card.id, "units": 1, "unit": "request", "money": 0},
                },
                usefulness=None,
            )
        )
        session.flush()

        if result.kind in (ResultKind.OK, ResultKind.EMPTY):
            store.mark_success(session, card.id, now)
        else:
            failures = health.consecutive_failures if health is not None else 0
            cfg = settings.intelligence
            pause = backoff_seconds(
                failures,
                base=cfg.failure_backoff_base_seconds,
                cap=cfg.failure_backoff_cap_seconds,
                retry_after=result.retry_after_seconds,
            )
            if result.kind is ResultKind.FAILED:
                store.mark_failure(session, card.id, now, result.detail or "failed", pause)
                entry["pause_seconds"] = pause

        new = 0
        newest: datetime | None = None
        for obs in result.observations:
            if store.save_observation(session, obs, request_id):
                new += 1
            if obs.published_at is not None and (newest is None or obs.published_at > newest):
                newest = obs.published_at
        store.record_feed_poll(
            session,
            card.id,
            now,
            status=status,
            item_count=len(result.observations),
            etag=result.etag,
            last_modified=result.last_modified,
            newest_published_at=newest,
        )
        entry.update(
            status=status,
            items=len(result.observations),
            new=new,
            detail=result.detail,
            invalid=result.invalid_items,
            http_status=result.http_status,
        )
        return entry
    except Exception as exc:  # even the bookkeeping must not stop the other feeds
        logger.exception("polling %s failed", card.id)
        entry.update(status="ERROR", detail=f"{type(exc).__name__}: {exc}")
        return entry


def _window_observations(
    session: Session, provider_ids: list[str], since: datetime
) -> list[Observation]:
    rows = session.scalars(
        select(IntelObservationRow)
        .where(
            IntelObservationRow.payload_type == "news_article",
            IntelObservationRow.provider_id.in_(provider_ids),
            IntelObservationRow.ingested_at >= since,
        )
        .order_by(IntelObservationRow.ingested_at, IntelObservationRow.observation_id)
    )
    return [store.observation_from_row(r) for r in rows]


def _title_of(obs: Observation) -> str:
    return (obs.title or "").lower()


def associate_articles(
    session: Session,
    settings: Settings,
    now: datetime,
    *,
    registry: Registry,
    observations: list[Observation],
    gazetteer: Gazetteer,
    rules: ResearchRules,
    report: NewsIngestReport,
) -> None:
    """Match the stored articles to every open market that has a searchable topic, locally."""
    pairs = repo.latest_snapshots_for_open_markets(
        session, settings.intelligence.max_markets_scanned
    )
    plans: dict[str, MarketPlan] = {}
    lowered = [(o, _title_of(o)) for o in observations]
    linked: set[str] = set()
    for market_row, _ in pairs:
        market = to_market(market_row)
        plan = plan_market(
            analyze_market(market.id, market.question, market.close_at, now, gazetteer, rules),
            gazetteer,
            now,
        )
        plans[market.id] = plan
        topic = plan.topic
        if topic is None:
            continue
        report.candidate_markets += 1
        primary = topic.primary.lower() if topic.primary else None
        for obs, title in lowered:
            if primary is not None and primary not in title:
                continue  # cheap pre-filter; the real test below is word-exact
            link = match_news(obs, topic, market.id, now)
            if link is not None and store.save_link(session, link, now):
                report.new_links += 1
                linked.add(market.id)
    report.markets_newly_linked = sorted(linked)
    subset = [(m, s) for m, s in pairs if m.id in linked]
    report.journaled = _journal_features(session, subset, plans, registry, now)


async def ingest_news(
    session: Session,
    settings: Settings,
    now: datetime,
    *,
    registry: Registry,
    providers: Mapping[str, Provider],
    gazetteer: Gazetteer,
    rules: ResearchRules | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    clock: Callable[[], datetime] | None = None,
) -> NewsIngestReport:
    """Poll every enabled news feed once, store what is new, associate it with the markets.

    The caller owns the transaction. A source that fails, is paused or is over budget is
    recorded in ``polls`` and skipped; the other sources and the association still run.

    ``clock`` is read once the polls are done: a provider stamps an article when its answer
    arrives, which is after ``now``, so the association must look at the world after the polls
    or every article just fetched would count as coming from the future.
    """
    report = NewsIngestReport()
    window = timedelta(days=window_days)
    cards = sorted(registry.providers_for(NEED), key=lambda c: c.id)
    for card in cards:
        entry = await _poll_one(session, settings, card, providers.get(card.id), now, window)
        report.polls.append(entry)
        report.new_observations += int(entry.get("new", 0))
    if not cards:
        return report
    observations = _window_observations(session, [c.id for c in cards], now - window)
    report.articles_in_window = len(observations)
    if observations:
        after_polls = max(now, clock()) if clock is not None else now
        associate_articles(
            session,
            settings,
            after_polls,
            registry=registry,
            observations=observations,
            gazetteer=gazetteer,
            rules=rules or load_rules(),
            report=report,
        )
    return report


def news_metrics(
    session: Session, registry: Registry, now: datetime, window_days: int = DEFAULT_WINDOW_DAYS
) -> dict[str, Any]:
    """What the news ingestion has really produced, counted from stored rows only."""
    feed_ids = sorted(c.id for c in registry.all() if c.provides(NEED))
    enabled_ids = sorted(c.id for c in registry.providers_for(NEED))
    base: dict[str, Any] = {
        "feed_providers": feed_ids,
        "feed_providers_enabled": enabled_ids,
        "window_days": window_days,
    }
    rows = session.execute(
        select(IntelObservationRow.provider_id, func.count())
        .where(IntelObservationRow.provider_id.in_(feed_ids or [""]))
        .group_by(IntelObservationRow.provider_id)
    ).all()
    base["articles_by_provider"] = {str(p): int(n) for p, n in rows}
    window = _window_observations(session, enabled_ids, now - timedelta(days=window_days))
    items = [
        NewsItem(
            observation_id=o.observation_id,
            url=o.source_url or "",
            title=o.title or "",
            source=(o.payload.domain or o.provider_id)
            if isinstance(o.payload, NewsArticlePayload)
            else o.provider_id,
            at=o.published_at or o.available_at or o.ingested_at,
        )
        for o in window
        if o.source_url and o.title
    ]
    clusters = cluster_items(items)
    sources_per_event = [len(c.sources) for c in clusters]
    base.update(
        {
            "articles_in_window": len(window),
            "unique_events_in_window": len(clusters),
            "events_with_several_sources": sum(1 for n in sources_per_event if n > 1),
            "articles_per_event": None if not clusters else round(len(items) / len(clusters), 3),
        }
    )
    links = session.execute(
        select(IntelMarketLinkRow.market_id, IntelMarketLinkRow.observation_id)
        .join(
            IntelObservationRow,
            IntelObservationRow.observation_id == IntelMarketLinkRow.observation_id,
        )
        .where(IntelObservationRow.provider_id.in_(feed_ids or [""]), store.active_link())
    ).all()
    per_market: dict[str, set[str]] = defaultdict(set)
    per_article: dict[str, set[str]] = defaultdict(set)
    for market_id, obs_id in links:
        per_market[market_id].add(obs_id)
        per_article[obs_id].add(market_id)
    base.update(
        {
            "market_links": len(links),
            "markets_with_news": len(per_market),
            "articles_linked_to_a_market": len(per_article),
            "articles_serving_several_markets": sum(1 for m in per_article.values() if len(m) > 1),
            "links_usable_for_prediction": session.scalar(
                select(func.count())
                .select_from(IntelMarketLinkRow)
                .join(
                    IntelObservationRow,
                    IntelObservationRow.observation_id == IntelMarketLinkRow.observation_id,
                )
                .where(
                    IntelObservationRow.provider_id.in_(feed_ids or [""]),
                    IntelMarketLinkRow.usable_for_prediction.is_(True),
                    store.active_link(),
                )
            )
            or 0,
        }
    )
    journal = session.scalars(select(IntelFeatureJournalRow.features)).all()
    base["feature_rows_with_news"] = sum(
        1 for feats in journal if any(str(f.get("name", "")).startswith("news.") for f in feats)
    )
    # Articles are one thing, markets served another: both are reported, neither is a score.
    return base


__all__ = [
    "NewsIngestReport",
    "associate_articles",
    "ingest_news",
    "news_metrics",
]

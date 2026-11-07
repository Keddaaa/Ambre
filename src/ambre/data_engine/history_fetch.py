"""Fetching the bet history of resolved markets (read-only GETs).

For each resolved YES/NO market without a stored history, every bet is read, newest first, page by
page, until a page is short (the first bet was reached) or the page cap is hit. A history that hit
the cap is stored as incomplete and never used. One market per transaction: an interruption
loses at most the market in progress, and a rerun resumes where it stopped.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ambre.evaluation_engine.history import bet_events, history_from_bets
from ambre.infrastructure import repositories as repo
from ambre.integrations.manifold.client import MAX_LIST_LIMIT, ManifoldClient, ManifoldError
from ambre.integrations.manifold.mapping import MappedMarket, map_market

UNIVERSE_SOURCE = "manifold_search_resolved"

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 30


@dataclass
class HistoryFetchReport:
    requested: int = 0
    complete: int = 0
    incomplete: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    bets: int = 0
    requests: int = 0


async def fetch_bet_history(
    client: ManifoldClient,
    market_id: str,
    *,
    page_limit: int = MAX_LIST_LIMIT,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool, int]:
    """All bets of a market and whether the first bet was reached; also the pages requested."""
    bets: list[dict[str, Any]] = []
    before: str | None = None
    for page_number in range(1, max_pages + 1):
        page = await client.list_bets(market_id, before=before, limit=page_limit)
        bets.extend(page)
        if len(page) < page_limit:
            return bets, True, page_number
        last = page[-1].get("id")
        if not isinstance(last, str):
            return bets, False, page_number  # cannot continue without a cursor
        before = last
    return bets, False, max_pages


async def fetch_histories(
    client: ManifoldClient,
    factory: sessionmaker[Session],
    clock: Callable[[], datetime],
    *,
    limit: int,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> HistoryFetchReport:
    report = HistoryFetchReport()
    with factory() as session:
        market_ids = repo.markets_needing_history(session, limit)
    report.requested = len(market_ids)
    for market_id in market_ids:
        try:
            bets, complete, pages = await fetch_bet_history(client, market_id, max_pages=max_pages)
        except ManifoldError as exc:  # includes ManifoldUnavailable: skip this market, go on
            logger.warning("history of %s not fetched: %s", market_id, exc)
            report.failed.append({"market_id": market_id, "reason": str(exc)})
            continue
        report.requests += pages
        report.bets += len(bets)
        history = history_from_bets(market_id, bets, complete=complete)
        with factory() as session:
            repo.save_price_history(session, history, len(bets), clock())
            session.commit()
        if complete:
            report.complete += 1
        else:
            report.incomplete += 1
    return report


async def fetch_market_bets(
    client: ManifoldClient,
    factory: sessionmaker[Session],
    clock: Callable[[], datetime],
    *,
    limit: int,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> HistoryFetchReport:
    """Store the bet-level history of resolved markets (ADR-022). One market per transaction, and
    a rerun resumes. ``market_price_points`` is not touched: ADR-018 stays reproducible."""
    report = HistoryFetchReport()
    with factory() as session:
        market_ids = repo.markets_needing_bets(session, limit)
    report.requested = len(market_ids)
    for done, market_id in enumerate(market_ids, start=1):
        try:
            bets, complete, pages = await fetch_bet_history(client, market_id, max_pages=max_pages)
        except ManifoldError as exc:  # includes ManifoldUnavailable: skip this market, go on
            logger.warning("bets of %s not fetched: %s", market_id, exc)
            report.failed.append({"market_id": market_id, "reason": str(exc)})
            continue
        report.requests += pages
        report.bets += len(bets)
        events, dropped = bet_events(bets)
        with factory() as session:
            repo.save_market_bets(session, market_id, events, complete, len(bets), dropped, clock())
            session.commit()
        if complete:
            report.complete += 1
        else:
            report.incomplete += 1
        if done % 250 == 0:
            logger.info("bets read for %d of %d markets", done, len(market_ids))
    return report


@dataclass
class UniverseReport:
    anchors: int = 0
    listed: int = 0
    stored: int = 0
    skipped_resolution: int = 0  # CANCEL, MKT and other non YES/NO resolutions
    skipped_unusable: int = 0  # not binary/cpmm-1/MANA, or no resolution date
    failed: list[dict[str, str]] = field(default_factory=list)


def universe_anchors(now: datetime, since: datetime, stride_days: int) -> list[int]:
    """Instants (ms), newest first, from ``now`` back to ``since``: one list request each, so
    the sample is spread over the whole period instead of piling up on the latest weeks."""
    anchors: list[int] = []
    t = now
    while t > since:
        anchors.append(int(t.timestamp() * 1000))
        t -= timedelta(days=stride_days)
    return anchors


async def load_resolved_universe(
    client: ManifoldClient,
    factory: sessionmaker[Session],
    clock: Callable[[], datetime],
    *,
    since: datetime,
    stride_days: int,
    per_anchor: int,
) -> UniverseReport:
    """Store resolved YES/NO binary markets sampled across time. Read-only toward Manifold; a
    market is stored without a snapshot (it is resolved: there is no live price to record)."""
    report = UniverseReport()
    seen: set[str] = set()
    anchors = universe_anchors(clock(), since, stride_days)
    report.anchors = len(anchors)
    for anchor in anchors:
        try:
            page = await client.search_resolved(before_time_ms=anchor, limit=per_anchor)
        except ManifoldError as exc:
            logger.warning("resolved markets before %s not listed: %s", anchor, exc)
            report.failed.append({"anchor_ms": str(anchor), "reason": str(exc)})
            continue
        report.listed += len(page)
        with factory() as session:
            for raw in page:
                market_id = raw.get("id") if isinstance(raw, dict) else None
                if not isinstance(market_id, str) or market_id in seen:
                    continue
                seen.add(market_id)
                mapped = map_market(raw, clock(), UNIVERSE_SOURCE)
                if not isinstance(mapped, MappedMarket) or mapped.market.resolution_at is None:
                    report.skipped_unusable += 1
                    continue
                if mapped.market.resolution not in ("YES", "NO"):
                    report.skipped_resolution += 1
                    continue
                repo.upsert_market(session, mapped.market, raw, clock())
                report.stored += 1
            session.commit()
    return report

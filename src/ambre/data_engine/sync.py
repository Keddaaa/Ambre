"""Incremental, resumable synchronisation of Manifold markets.

Markets are listed newest-update first (`sort=updated-time`, `order=desc`) and paged with the
`before=<last id>` cursor. Each page is processed in its own transaction, so a failure on page N
keeps pages 1..N-1. The `high_watermark_ms` (max `lastUpdatedTime` seen) is only advanced once the
run reached a safe stopping point; otherwise the next run re-reads from the top, which is
idempotent thanks to snapshot deduplication.

Classification of a `Rejected` market:
- ignored: valid but out of scope (`reason` starts with "unsupported ", e.g. non-BINARY outcomeType,
  non cpmm-1 mechanism, CASH token) and both `outcome_type` and `market_id` are known. Stored via
  `upsert_ignored` with its reason, never evaluated.
- invalid: anything else (missing/invalid fields, no id, no outcomeType, no mechanism). Counted,
  never stored, never fatal for the page.
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ambre.domain.models import MarketSnapshot
from ambre.infrastructure.repositories import (
    get_sync_state,
    insert_snapshot_if_new,
    latest_snapshot_for_market,
    save_sync_state,
    upsert_ignored,
    upsert_market,
)
from ambre.integrations.manifold.client import ManifoldClient, ManifoldError
from ambre.integrations.manifold.mapping import MappedMarket, Rejected, map_market

SOURCE_ID = "manifold:/v0/markets"
SYNC_KEY = "markets"

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    pages: int = 0
    stored_markets: int = 0
    new_snapshots: int = 0
    duplicate_snapshots: int = 0
    ignored: int = 0
    invalid: int = 0
    completed: bool = False
    error: str | None = None


def _updated_ms(raw: dict[str, Any]) -> int | None:
    value = raw.get("lastUpdatedTime")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return int(value)


def _is_ignored(raw: dict[str, Any], rejected: Rejected) -> bool:
    return (
        rejected.reason.startswith("unsupported ")
        and rejected.market_id is not None
        and rejected.outcome_type is not None
        and raw.get("mechanism") is not None
    )


class MarketSyncService:
    def __init__(
        self,
        client: ManifoldClient,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._clock = clock

    async def sync(self, *, max_pages: int = 5, page_size: int = 1000) -> SyncReport:
        report = SyncReport()
        with self._session_factory() as s:
            state = get_sync_state(s, SYNC_KEY)
            watermark = state.high_watermark_ms if state is not None else None

        seen_max: int | None = None
        before: str | None = None
        caught_up = False
        for _ in range(max_pages):
            try:
                page = await self._client.list_markets(limit=page_size, before=before)
            except ManifoldError as exc:
                return self._fail(report, str(exc))
            observed_at = self._clock()
            self._store_page(page, observed_at, report)
            report.pages += 1

            times = [t for t in (_updated_ms(m) for m in page) if t is not None]
            if times:
                seen_max = max(times) if seen_max is None else max(seen_max, max(times))
            if watermark is not None and any(t <= watermark for t in times):
                caught_up = True
            if caught_up or len(page) < page_size:
                caught_up = True
                break
            last_id = page[-1].get("id")
            if not isinstance(last_id, str):
                return self._fail(report, "cannot paginate: last market of page has no id")
            before = last_id

        if not caught_up and watermark is not None:
            # Markets between the old watermark and the oldest page read were not reached:
            # advancing would skip them for good.
            return self._fail(report, "max_pages reached before the watermark; not advancing")

        new_mark = watermark if seen_max is None else max(seen_max, watermark or seen_max)
        now = self._clock()
        with self._session_factory.begin() as s:
            save_sync_state(
                s,
                SYNC_KEY,
                high_watermark_ms=new_mark,
                last_success_at=now,
                last_error=None,
                updated_at=now,
            )
        report.completed = True
        return report

    def _fail(self, report: SyncReport, message: str) -> SyncReport:
        """Record the error but leave watermark and last_success_at untouched."""
        report.completed = False
        report.error = message
        with self._session_factory.begin() as s:
            state = get_sync_state(s, SYNC_KEY)
            save_sync_state(
                s,
                SYNC_KEY,
                high_watermark_ms=state.high_watermark_ms if state else None,
                last_success_at=state.last_success_at if state else None,
                last_error=message,
                updated_at=self._clock(),
            )
        logger.warning("market sync incomplete: %s", message)
        return report

    def _store_page(
        self, page: list[dict[str, Any]], observed_at: datetime, report: SyncReport
    ) -> None:
        counts = SyncReport()
        with self._session_factory.begin() as s:
            ingested_at = self._clock()
            for raw in page:
                result = map_market(raw, observed_at, SOURCE_ID)
                if isinstance(result, Rejected):
                    if _is_ignored(raw, result):
                        assert result.market_id is not None and result.outcome_type is not None
                        upsert_ignored(
                            s, result.market_id, result.outcome_type, result.reason, observed_at
                        )
                        counts.ignored += 1
                    else:
                        counts.invalid += 1
                        logger.info("invalid market %s: %s", result.market_id, result.reason)
                    continue
                self._store_market(s, result, raw, observed_at, ingested_at, counts)
        report.stored_markets += counts.stored_markets
        report.new_snapshots += counts.new_snapshots
        report.duplicate_snapshots += counts.duplicate_snapshots
        report.ignored += counts.ignored
        report.invalid += counts.invalid

    @staticmethod
    def _store_market(
        s: Session,
        mapped: MappedMarket,
        raw: dict[str, Any],
        observed_at: datetime,
        ingested_at: datetime,
        counts: SyncReport,
    ) -> None:
        upsert_market(s, mapped.market, raw, observed_at)
        counts.stored_markets += 1
        snap = mapped.snapshot
        duplicate = (
            _same_values(s, snap)
            if snap.event_at is None
            else insert_snapshot_if_new(s, snap, ingested_at) is None
        )
        if duplicate:
            counts.duplicate_snapshots += 1
            return
        if snap.event_at is None:
            insert_snapshot_if_new(s, snap, ingested_at)
        counts.new_snapshots += 1


def _same_values(s: Session, snap: MarketSnapshot) -> bool:
    last = latest_snapshot_for_market(s, snap.market_id)
    return (
        last is not None
        and last.probability == snap.probability
        and last.pool_yes == snap.pool_yes
        and last.pool_no == snap.pool_no
    )

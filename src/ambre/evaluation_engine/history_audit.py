"""Audit of the bet-level history (pure, no I/O; ADR-022).

Each check answers one question: is the rebuilt price the one Manifold showed, is every timestamp
consistent, and which fields carry the future? The price chain is the strongest test: every bet
must start from the price the previous bet left, so a missing move cannot hide.
"""

import bisect
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ambre.evaluation_engine.history import BetLevelMarket, PriceHistory, PricePoint, price_at

CHAIN_TOLERANCE = 1e-6
PRICE_TOLERANCE = 0.01
SAME_INSTANT_SECONDS = 60.0
HORIZONS_DAYS = (1, 3, 7, 30)


@dataclass(frozen=True)
class LiveSnapshot:
    """A price Ambre recorded live: evidence that does not come from the bets."""

    market_id: str
    at: datetime
    probability: float


@dataclass(frozen=True)
class PriceShift:
    horizon_days: int
    n: int
    moved_over_1pt: int
    moved_over_5pt: int
    mean_abs_difference: float | None
    #: mean of (corrected - former) signed towards the outcome: positive means the former prices
    #: were further from what happened
    mean_shift_towards_outcome: float | None


@dataclass(frozen=True)
class HistoryAudit:
    markets: int
    complete: int
    markets_without_bets: int
    events: int
    raw_bets: int
    dropped: int
    events_without_amount: int
    events_without_bettor: int
    chain_breaks: int
    markets_with_chain_breaks: int
    same_instant_events: int
    events_before_creation: int
    events_after_resolution: int
    final_price_compared: int
    final_price_mismatches: int
    close_equals_resolution: int
    close_equals_resolution_share: float | None
    close_after_resolution: int
    snapshots_compared: int
    snapshot_mismatches: int
    fetched_before_resolution: int
    lifetime_days_quantiles: dict[str, float]
    #: share of a cohort still unresolved at each age, from the oldest two creation years
    expected_unresolved_at_age_days: dict[str, float]
    yes_rate_by_creation_year: dict[str, float]
    yes_rate_by_resolution_year: dict[str, float]
    old_vs_corrected: list[PriceShift] = field(default_factory=list)


def _chain_breaks(m: BetLevelMarket) -> int:
    return sum(
        1
        for a, b in zip(m.events, m.events[1:], strict=False)
        if abs(a.prob_after - b.prob_before) > CHAIN_TOLERANCE
    )


def _rate(rows: Sequence[tuple[int, bool]]) -> dict[str, float]:
    years: dict[int, list[bool]] = {}
    for year, outcome in rows:
        years.setdefault(year, []).append(outcome)
    return {str(y): sum(v) / len(v) for y, v in sorted(years.items())}


def _price_from_events(m: BetLevelMarket, at: datetime) -> float | None:
    history = PriceHistory(
        m.market_id,
        m.events[0].prob_before if m.events else None,
        tuple(PricePoint(e.at, e.prob_after, e.bet_id) for e in m.events),
        m.complete,
    )
    return price_at(history, at)


def _shifts(
    markets: Sequence[BetLevelMarket],
    old: Mapping[str, tuple[float | None, tuple[PricePoint, ...]]],
) -> list[PriceShift]:
    out = []
    for horizon in HORIZONS_DAYS:
        diffs: list[float] = []
        toward: list[float] = []
        for m in markets:
            if m.market_id not in old or not m.complete:
                continue
            at = m.created_at + timedelta(days=horizon)
            if at >= m.resolution_at or (m.close_at is not None and at >= m.close_at):
                continue
            initial, points = old[m.market_id]
            former = price_at(PriceHistory(m.market_id, initial, points, True), at)
            corrected = _price_from_events(m, at)
            if former is None or corrected is None:
                continue
            diffs.append(abs(corrected - former))
            toward.append((corrected - former) * (1.0 if m.outcome else -1.0))
        out.append(
            PriceShift(
                horizon_days=horizon,
                n=len(diffs),
                moved_over_1pt=sum(1 for d in diffs if d > 0.01),
                moved_over_5pt=sum(1 for d in diffs if d > 0.05),
                mean_abs_difference=sum(diffs) / len(diffs) if diffs else None,
                mean_shift_towards_outcome=sum(toward) / len(toward) if toward else None,
            )
        )
    return out


def _quantiles(values: Sequence[float]) -> dict[str, float]:
    if len(values) < 2:
        return {}
    q = statistics.quantiles(values, n=10, method="inclusive")
    return {"p10": q[0], "p50": q[4], "p90": q[8]}


def _expected_unresolved(
    markets: Sequence[BetLevelMarket], ages: Sequence[int] = (60, 120, 215, 305, 489)
) -> dict[str, float]:
    """A resolved-only sample loses the long markets of recent cohorts. The two oldest creation
    years show how long markets take; those cohorts are themselves truncated, so this is a floor."""
    years = sorted({m.created_at.year for m in markets})
    if not years:
        return {}
    oldest = set(years[:2])
    lives = sorted(
        (m.resolution_at - m.created_at).total_seconds() / 86400.0
        for m in markets
        if m.created_at.year in oldest
    )
    return {str(a): 1.0 - bisect.bisect_right(lives, a) / len(lives) for a in ages}


def audit_history(
    markets: Sequence[BetLevelMarket],
    former_points: Mapping[str, tuple[float | None, tuple[PricePoint, ...]]],
    snapshots: Sequence[LiveSnapshot],
) -> HistoryAudit:
    complete = [m for m in markets if m.complete]
    by_id = {m.market_id: m for m in complete}
    breaks = [_chain_breaks(m) for m in complete]
    compared = [m for m in complete if m.events and m.final_probability is not None]
    same_instant = sum(
        1 for m in complete for a, b in zip(m.events, m.events[1:], strict=False) if a.at == b.at
    )
    closes = [m for m in complete if m.close_at is not None]
    equal = [
        m
        for m in closes
        if m.close_at is not None
        and abs((m.close_at - m.resolution_at).total_seconds()) <= SAME_INSTANT_SECONDS
    ]
    checked = mismatched = 0
    for s in snapshots:
        market = by_id.get(s.market_id)
        if market is None or not market.events:
            continue
        rebuilt = _price_from_events(market, s.at)
        if rebuilt is None:
            continue
        checked += 1
        mismatched += abs(rebuilt - s.probability) > PRICE_TOLERANCE
    lives = [(m.resolution_at - m.created_at).total_seconds() / 86400.0 for m in complete]
    return HistoryAudit(
        markets=len(markets),
        complete=len(complete),
        markets_without_bets=sum(1 for m in complete if not m.events),
        events=sum(len(m.events) for m in complete),
        raw_bets=sum(m.bet_count for m in complete),
        dropped=sum(m.dropped for m in complete),
        events_without_amount=sum(1 for m in complete for e in m.events if e.amount is None),
        events_without_bettor=sum(1 for m in complete for e in m.events if e.bettor is None),
        chain_breaks=sum(breaks),
        markets_with_chain_breaks=sum(1 for b in breaks if b),
        same_instant_events=same_instant,
        events_before_creation=sum(1 for m in complete for e in m.events if e.at < m.created_at),
        events_after_resolution=sum(
            1 for m in complete for e in m.events if e.at > m.resolution_at
        ),
        final_price_compared=len(compared),
        final_price_mismatches=sum(
            1
            for m in compared
            if m.final_probability is not None
            and abs(m.events[-1].prob_after - m.final_probability) > PRICE_TOLERANCE
        ),
        close_equals_resolution=len(equal),
        close_equals_resolution_share=len(equal) / len(closes) if closes else None,
        close_after_resolution=sum(
            1
            for m in closes
            if m.close_at is not None
            and (m.close_at - m.resolution_at).total_seconds() > SAME_INSTANT_SECONDS
        ),
        snapshots_compared=checked,
        snapshot_mismatches=mismatched,
        fetched_before_resolution=sum(1 for m in complete if m.fetched_at < m.resolution_at),
        lifetime_days_quantiles=_quantiles(lives),
        expected_unresolved_at_age_days=_expected_unresolved(complete),
        yes_rate_by_creation_year=_rate([(m.created_at.year, m.outcome) for m in complete]),
        yes_rate_by_resolution_year=_rate([(m.resolution_at.year, m.outcome) for m in complete]),
        old_vs_corrected=_shifts(complete, former_points),
    )

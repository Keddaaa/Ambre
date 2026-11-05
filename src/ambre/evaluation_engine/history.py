"""Price history of a market rebuilt from its bets (pure, no I/O).

Manifold's ``GET /v0/bets`` gives, for every bet, the market probability just before
(``probBefore``) and just after (``probAfter``) it, with its creation time. The price of a market at
any past instant ``t`` is therefore the ``probAfter`` of the last bet created at or before
``t``, and the opening probability (the first bet's ``probBefore``) before any bet. Nothing is
estimated or interpolated, and nothing later than ``t`` is read.

A history that was not fetched to its very first bet is *incomplete* and gives no price at all:
its early prices would be guessed. A malformed bet is dropped and counted, never repaired.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class PricePoint:
    at: datetime
    probability: float
    bet_id: str


@dataclass(frozen=True)
class PriceHistory:
    market_id: str
    #: opening probability (the first valid bet's ``probBefore``); None without any valid bet
    initial_probability: float | None
    points: tuple[PricePoint, ...]
    complete: bool
    dropped: int = 0


def _probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    v = float(value)
    return v if math.isfinite(v) and 0.0 <= v <= 1.0 else None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def history_from_bets(market_id: str, bets: Sequence[Any], *, complete: bool) -> PriceHistory:
    """Build a history from raw Manifold bets, whatever their order."""
    rows: list[tuple[datetime, str, float, float]] = []
    dropped = 0
    for raw in bets:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        if raw.get("isCancelled") is True or raw.get("isRedemption") is True:
            continue  # a cancelled or redeemed bet does not move the price
        bet_id = raw.get("id") or raw.get("betId")
        at = _timestamp(raw.get("createdTime"))
        before = _probability(raw.get("probBefore"))
        after = _probability(raw.get("probAfter"))
        if not isinstance(bet_id, str) or at is None or before is None or after is None:
            dropped += 1
            continue
        rows.append((at, bet_id, before, after))
    rows.sort(key=lambda r: (r[0], r[1]))
    return PriceHistory(
        market_id=market_id,
        initial_probability=rows[0][2] if rows else None,
        points=tuple(PricePoint(at, after, bet_id) for at, bet_id, _, after in rows),
        complete=complete,
        dropped=dropped,
    )


def price_at(history: PriceHistory, instant: datetime) -> float | None:
    """The market probability at ``instant``, or None when it cannot be known."""
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    if not history.complete or history.initial_probability is None:
        return None
    price = history.initial_probability
    for point in history.points:  # ascending: stop at the first bet after the instant
        if point.at > instant:
            break
        price = point.probability
    return price

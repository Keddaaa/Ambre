"""Price history of a market rebuilt from its bets (pure, no I/O).

Manifold's ``GET /v0/bets`` gives, for every bet, the market probability just before
(``probBefore``) and just after (``probAfter``) it, with its creation time. The price of a market at
any past instant ``t`` is therefore the ``probAfter`` of the last bet created at or before
``t``, and the opening probability (the first bet's ``probBefore``) before any bet. Nothing is
estimated or interpolated, and nothing later than ``t`` is read.

A history that was not fetched to its very first bet is *incomplete* and gives no price at all:
its early prices would be guessed. A malformed bet is dropped and counted, never repaired.

A cancelled order is not a bet that never happened: a limit order can be partly filled, move the
price, and have only its remainder cancelled. It is ignored only if it left the price unchanged.

``amount`` is a final value: a resting order keeps filling after it was placed. The volume of a bet
at its own instant comes from the fills stamped at that instant (``BetEvent.amount``). A later fill
is the other side of a later bet, which counts it, so nothing is lost or counted twice.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: a taker's fills carry the bet's own timestamp, within the time of one transaction
FILL_TOLERANCE_MS = 1000


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


@dataclass(frozen=True)
class BetEvent:
    """One bet that counts, as known at its own instant."""

    bet_id: str
    at: datetime
    prob_before: float
    prob_after: float
    #: Mana traded at this instant; None when the raw bet does not tell
    amount: float | None
    #: anonymous key of the bettor; None when the raw bet does not tell
    bettor: str | None
    is_api: bool | None


@dataclass(frozen=True)
class BetLevelMarket:
    """A resolved market with its bet-level read (ADR-022).

    ``close_at`` and ``final_probability`` are read after the resolution: audit and selection only,
    never a feature (Manifold rewrites ``closeTime`` when a market resolves early).
    """

    market_id: str
    question: str
    creator_id: str
    created_at: datetime
    close_at: datetime | None
    resolution_at: datetime
    outcome: bool
    complete: bool
    bet_count: int
    dropped: int
    fetched_at: datetime
    events: tuple[BetEvent, ...]
    final_probability: float | None


def _anonymous(user_id: object) -> str | None:
    if not isinstance(user_id, str) or not user_id:
        return None
    return hashlib.sha256(user_id.encode()).hexdigest()[:16]


def _traded_amount(raw: dict[str, Any]) -> float | None:
    """The Mana this bet traded at its own instant, never what filled later."""
    fills = raw.get("fills")
    if isinstance(fills, list):
        created = raw.get("createdTime")
        total = 0.0
        for fill in fills:
            if not isinstance(fill, dict):
                return None
            amount, stamp = fill.get("amount"), fill.get("timestamp")
            if not isinstance(amount, int | float) or not isinstance(stamp, int | float):
                return None
            if not isinstance(created, int | float):
                return None
            if stamp <= created + FILL_TOLERANCE_MS:
                total += abs(float(amount))
        return total
    if raw.get("limitProb") is not None:
        return None  # a limit order without fills may hold later ones in its amount
    amount = raw.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int | float):
        return None
    return abs(float(amount)) if math.isfinite(amount) else None


def bet_events(bets: Sequence[Any]) -> tuple[list[BetEvent], int]:
    """The bets that count, oldest first, and how many were malformed."""
    events: list[BetEvent] = []
    dropped = 0
    for raw in bets:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        if raw.get("isRedemption") is True:
            continue  # a redemption nets out shares: the price does not move
        bet_id = raw.get("id") or raw.get("betId")
        at = _timestamp(raw.get("createdTime"))
        before = _probability(raw.get("probBefore"))
        after = _probability(raw.get("probAfter"))
        if not isinstance(bet_id, str) or at is None or before is None or after is None:
            dropped += 1
            continue
        if raw.get("isCancelled") is True and before == after:
            continue  # cancelled without a fill: it never traded
        is_api = raw.get("isApi")
        events.append(
            BetEvent(
                bet_id=bet_id,
                at=at,
                prob_before=before,
                prob_after=after,
                amount=_traded_amount(raw),
                bettor=_anonymous(raw.get("userId")),
                is_api=is_api if isinstance(is_api, bool) else None,
            )
        )
    events.sort(key=lambda e: (e.at, e.bet_id))
    return events, dropped


def history_from_events(
    market_id: str, events: Sequence[BetEvent], *, complete: bool, dropped: int = 0
) -> PriceHistory:
    """The price history behind sorted events."""
    return PriceHistory(
        market_id=market_id,
        initial_probability=events[0].prob_before if events else None,
        points=tuple(PricePoint(e.at, e.prob_after, e.bet_id) for e in events),
        complete=complete,
        dropped=dropped,
    )


def history_from_bets(market_id: str, bets: Sequence[Any], *, complete: bool) -> PriceHistory:
    """Build a history from raw Manifold bets, whatever their order."""
    events, dropped = bet_events(bets)
    return history_from_events(market_id, events, complete=complete, dropped=dropped)


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

"""Historical features of a resolved market at a past instant (pure, no I/O; ADR-022).

``features_at`` receives the creation date, the bets and the instant, nothing else: it cannot see
the close date, the resolution or the final market fields, and it reads no bet after the instant.
A value that cannot be known stays ``None``; a count that really is zero stays 0.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ambre.evaluation_engine.history import BetEvent
from ambre.prediction_engine.logistic import anchored_logit as logit

Features = dict[str, float | None]
DAY = timedelta(days=1)


class Availability(StrEnum):
    #: computed from bets made up to the instant
    POINT_IN_TIME = "POINT_IN_TIME"
    #: fixed when the market was created
    CREATION_TIME = "CREATION_TIME"
    #: read after the resolution: refused as a feature
    FINAL_VALUE = "FINAL_VALUE"


@dataclass(frozen=True)
class HistoricalFeatureSpec:
    name: str
    definition: str
    availability: Availability
    missing_policy: str


_KNOWN = "always known when the market has a bet"
_NONE = "None when it cannot be computed, never 0"
_REFUSED = "refused: never computed"
_PIT = Availability.POINT_IN_TIME
_FINAL = Availability.FINAL_VALUE

HISTORICAL_FEATURES: tuple[HistoricalFeatureSpec, ...] = (
    HistoricalFeatureSpec("price", "probAfter of the last bet up to the instant", _PIT, _KNOWN),
    HistoricalFeatureSpec("age_days", "days since creation", Availability.CREATION_TIME, _KNOWN),
    HistoricalFeatureSpec("logit_change_24h", "logit move over the last 24 h", _PIT, _NONE),
    HistoricalFeatureSpec("logit_change_open", "logit move since the opening price", _PIT, _KNOWN),
    HistoricalFeatureSpec("realized_volatility", "RMS of the logit move of each bet", _PIT, _NONE),
    HistoricalFeatureSpec("log_trades", "log1p of bets that traded", _PIT, _NONE),
    HistoricalFeatureSpec("log_trades_24h", "same, last 24 h", _PIT, _NONE),
    HistoricalFeatureSpec("log_volume", "log1p of Mana traded at each bet's instant", _PIT, _NONE),
    HistoricalFeatureSpec("log_volume_24h", "same, last 24 h", _PIT, _NONE),
    HistoricalFeatureSpec("log_bettors", "log1p of distinct bettors", _PIT, _NONE),
    HistoricalFeatureSpec("log_hours_since_trade", "log1p of hours since a trade", _PIT, _NONE),
    HistoricalFeatureSpec("logit_x_log_trades", "logit(price) * log_trades", _PIT, _NONE),
    HistoricalFeatureSpec(
        "seconds_to_close",
        "closeTime is rewritten to the resolution when a market resolves early",
        _FINAL,
        _REFUSED,
    ),
    HistoricalFeatureSpec(
        "total_liquidity", "markets.raw is read after the resolution", _FINAL, _REFUSED
    ),
    HistoricalFeatureSpec("final_volume", "markets.raw volume", _FINAL, _REFUSED),
    HistoricalFeatureSpec("final_bettors", "markets.raw uniqueBettorCount", _FINAL, _REFUSED),
)

PATH_FEATURES: tuple[str, ...] = ("logit_change_24h", "logit_change_open", "realized_volatility")
ACTIVITY_FEATURES: tuple[str, ...] = (
    "log_trades",
    "log_trades_24h",
    "log_volume",
    "log_volume_24h",
    "log_bettors",
    "log_hours_since_trade",
    "logit_x_log_trades",
)


def _traded(e: BetEvent) -> bool:
    """Unknown counts as a trade: only a known zero is an order that traded nothing."""
    return e.amount is None or e.amount > 0.0


def _volume(events: Sequence[BetEvent]) -> float | None:
    total = 0.0
    for e in events:
        if e.amount is None:
            return None
        total += e.amount
    return math.log1p(total)


def _bettors(events: Sequence[BetEvent]) -> float | None:
    keys = set()
    for e in events:
        if _traded(e):
            if e.bettor is None:
                return None
            keys.add(e.bettor)
    return math.log1p(len(keys))


def features_at(created_at: datetime, events: Sequence[BetEvent], at: datetime) -> Features:
    """What was knowable at ``at``. No bet at all: nothing to describe, not even a price."""
    if at.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    if not events:
        return {}
    ordered = sorted(events, key=lambda e: (e.at, e.bet_id))
    opening = ordered[0].prob_before
    past = [e for e in ordered if e.at <= at]
    price = past[-1].prob_after if past else opening
    since = at - DAY
    recent = [e for e in past if e.at > since]
    trades = [e for e in past if _traded(e)]
    recent_trades = [e for e in recent if _traded(e)]
    steps = [logit(e.prob_after) - logit(e.prob_before) for e in past]
    change_24h = None
    if since >= created_at:
        before = [e for e in past if e.at <= since]
        change_24h = logit(price) - logit(before[-1].prob_after if before else opening)
    log_trades = math.log1p(len(trades))
    hours_since = None
    if trades:
        hours_since = math.log1p((at - trades[-1].at).total_seconds() / 3600.0)
    return {
        "price": price,
        "age_days": (at - created_at).total_seconds() / 86400.0,
        "logit_change_24h": change_24h,
        "logit_change_open": logit(price) - logit(opening),
        "realized_volatility": (
            math.sqrt(sum(s * s for s in steps) / len(steps)) if steps else None
        ),
        "log_trades": log_trades,
        "log_trades_24h": math.log1p(len(recent_trades)),
        "log_volume": _volume(past),
        "log_volume_24h": _volume(recent),
        "log_bettors": _bettors(past),
        "log_hours_since_trade": hours_since,
        "logit_x_log_trades": logit(price) * log_trades,
    }

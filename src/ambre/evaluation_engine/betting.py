"""Bet-level statistics of a simulation: win rate, stakes, P&L (pure, no I/O).

One bet is one position: the money actually committed. A rejected decision carries a
proposed stake that was never spent, so it only appears in the ``DecisionFunnel``.

A bet is won or lost on money (payout against stake), which also covers partial MKT
resolutions. Refunds and break-even bets stay out of the win rate. Nothing here proves
an edge: a small sample says nothing (``sufficient_sample``, ``win_rate_ci95``).
"""

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ambre.evaluation_engine.metrics import MIN_SAMPLE

Z_95 = 1.959964
RECENT_LIMIT = 20
ENTRY_PRICE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.10),
    (0.10, 0.25),
    (0.25, 0.50),
    (0.50, 0.75),
    (0.75, 1.0),
)


@dataclass(frozen=True)
class BetRecord:
    market_id: str
    question: str | None
    side: str
    stake: float
    shares: float
    placed_at: datetime
    settled_at: datetime | None
    payout: float | None
    resolution: str | None
    #: Value of an open bet at the latest price; ``None`` when the price is unknown.
    current_value: float | None
    #: The model's probability that this side wins, as of the decision.
    estimated_win_probability: float | None

    @property
    def is_settled(self) -> bool:
        return self.settled_at is not None and self.payout is not None

    @property
    def is_refund(self) -> bool:
        return self.is_settled and self.resolution == "CANCEL"

    @property
    def pnl(self) -> float | None:
        return None if self.payout is None else self.payout - self.stake

    @property
    def is_won(self) -> bool:
        return self.is_settled and not self.is_refund and (self.pnl or 0.0) > 0.0

    @property
    def is_lost(self) -> bool:
        return self.is_settled and not self.is_refund and (self.pnl or 0.0) < 0.0

    @property
    def entry_price(self) -> float:
        return self.stake / self.shares


@dataclass(frozen=True)
class DecisionFunnel:
    total: int
    simulated: int
    rejected: int
    no_trade: int


@dataclass(frozen=True)
class BetLine:
    market_id: str
    question: str | None
    side: str
    stake: float
    entry_price: float
    payout: float
    pnl: float
    resolution: str | None
    settled_at: datetime


@dataclass(frozen=True)
class BetGroup:
    label: str
    bets: int
    won: int
    lost: int
    win_rate: float | None
    staked: float
    realized_pnl: float | None


@dataclass(frozen=True)
class PnlPoint:
    index: int
    at: datetime
    pnl: float
    cumulative_pnl: float


@dataclass(frozen=True)
class DayCount:
    day: str
    bets: int
    staked: float


@dataclass(frozen=True)
class BettingStats:
    decisions: DecisionFunnel
    bets: int
    open: int
    settled: int
    won: int
    lost: int
    pushed: int
    refunded: int
    win_rate: float | None
    win_rate_ci95: tuple[float, float] | None
    market_implied_win_rate: float | None
    model_implied_win_rate: float | None
    sufficient_sample: bool
    min_sample: int
    total_staked: float
    open_stake: float
    average_stake: float | None
    median_stake: float | None
    largest_stake: float | None
    smallest_stake: float | None
    realized_pnl: float | None
    unrealized_pnl: float | None
    total_pnl: float | None
    roi: float | None
    average_win: float | None
    average_loss: float | None
    profit_factor: float | None
    best_bet: BetLine | None
    worst_bet: BetLine | None
    longest_win_streak: int
    longest_loss_streak: int
    #: Positive for a run of wins, negative for a run of losses, 0 before any result.
    current_streak: int
    by_side: list[BetGroup]
    by_entry_price: list[BetGroup]
    pnl_curve: list[PnlPoint]
    bets_per_day: list[DayCount]
    recent_settled: list[BetLine]


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval: stays meaningful at 0 % or 100 % on a small sample."""
    if n <= 0:
        raise ValueError("wilson_interval needs at least one observation")
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _win_rate(won: int, lost: int) -> float | None:
    return won / (won + lost) if won + lost else None


def _realized(bets: Sequence[BetRecord]) -> float | None:
    settled = [b.pnl for b in bets if b.is_settled and b.pnl is not None]
    return sum(settled) if settled else None


def _line(b: BetRecord) -> BetLine:
    assert b.payout is not None and b.settled_at is not None and b.pnl is not None
    return BetLine(
        market_id=b.market_id,
        question=b.question,
        side=b.side,
        stake=b.stake,
        entry_price=b.entry_price,
        payout=b.payout,
        pnl=b.pnl,
        resolution=b.resolution,
        settled_at=b.settled_at,
    )


def _group(label: str, bets: Sequence[BetRecord]) -> BetGroup:
    won = sum(b.is_won for b in bets)
    lost = sum(b.is_lost for b in bets)
    return BetGroup(
        label=label,
        bets=len(bets),
        won=won,
        lost=lost,
        win_rate=_win_rate(won, lost),
        staked=sum(b.stake for b in bets),
        realized_pnl=_realized(bets),
    )


def _in_bucket(price: float, lower: float, upper: float) -> bool:
    if upper >= ENTRY_PRICE_BUCKETS[-1][1]:
        return price >= lower
    return lower <= price < upper


def _bucket_label(lower: float, upper: float) -> str:
    return f"{lower * 100:.0f}–{upper * 100:.0f} %"


def _longest_run(results: Sequence[bool], target: bool) -> int:
    longest = run = 0
    for result in results:
        run = run + 1 if result is target else 0
        longest = max(longest, run)
    return longest


def _current_streak(results: Sequence[bool]) -> int:
    if not results:
        return 0
    last = results[-1]
    run = 0
    for result in reversed(results):
        if result is not last:
            break
        run += 1
    return run if last else -run


def _settlement_key(b: BetRecord) -> tuple[datetime, datetime, str]:
    assert b.settled_at is not None
    return b.settled_at, b.placed_at, b.market_id


def _pnl_curve(settled: Sequence[BetRecord]) -> list[PnlPoint]:
    curve: list[PnlPoint] = []
    cumulative = 0.0
    for b in settled:
        assert b.settled_at is not None and b.pnl is not None
        cumulative += b.pnl
        curve.append(PnlPoint(len(curve) + 1, b.settled_at, b.pnl, cumulative))
    return curve


def _bets_per_day(bets: Sequence[BetRecord]) -> list[DayCount]:
    days: dict[str, list[BetRecord]] = {}
    for b in bets:
        days.setdefault(b.placed_at.astimezone(UTC).date().isoformat(), []).append(b)
    return [
        DayCount(day, len(rows), sum(b.stake for b in rows)) for day, rows in sorted(days.items())
    ]


def _unrealized(open_bets: Sequence[BetRecord]) -> float | None:
    values = [b.current_value for b in open_bets]
    if not open_bets or any(v is None for v in values):
        return None
    return sum(b.current_value - b.stake for b in open_bets if b.current_value is not None)


def _total_pnl(
    open_bets: Sequence[BetRecord], realized: float | None, unrealized: float | None
) -> float | None:
    if open_bets and unrealized is None:
        return None
    if realized is None and unrealized is None:
        return None
    return (realized or 0.0) + (unrealized or 0.0)


def _extreme(bets: Sequence[BetRecord], pick: Callable[..., BetRecord]) -> BetLine | None:
    return _line(pick(bets, key=lambda b: b.pnl)) if bets else None


def betting_stats(
    bets: Sequence[BetRecord],
    decisions: DecisionFunnel,
    min_sample: int = MIN_SAMPLE,
    recent_limit: int = RECENT_LIMIT,
) -> BettingStats:
    settled = sorted((b for b in bets if b.is_settled), key=_settlement_key)
    open_bets = [b for b in bets if not b.is_settled]
    wins = [b for b in settled if b.is_won]
    losses = [b for b in settled if b.is_lost]
    decided = [b for b in settled if b.is_won or b.is_lost]
    refunded = sum(b.is_refund for b in settled)
    stakes = [b.stake for b in bets]
    realized = _realized(settled)
    unrealized = _unrealized(open_bets)
    at_risk = sum(b.stake for b in settled if not b.is_refund)
    gross_win = sum(b.pnl or 0.0 for b in wins)
    gross_loss = -sum(b.pnl or 0.0 for b in losses)
    estimates = [b.estimated_win_probability for b in decided]
    results = [b.is_won for b in decided]
    return BettingStats(
        decisions=decisions,
        bets=len(bets),
        open=len(open_bets),
        settled=len(settled),
        won=len(wins),
        lost=len(losses),
        pushed=len(settled) - len(decided) - refunded,
        refunded=refunded,
        win_rate=_win_rate(len(wins), len(losses)),
        win_rate_ci95=wilson_interval(len(wins), len(decided)) if decided else None,
        market_implied_win_rate=_mean([b.entry_price for b in decided]),
        model_implied_win_rate=(
            None if None in estimates else _mean([e for e in estimates if e is not None])
        ),
        sufficient_sample=len(decided) >= min_sample,
        min_sample=min_sample,
        total_staked=sum(stakes),
        open_stake=sum(b.stake for b in open_bets),
        average_stake=_mean(stakes),
        median_stake=statistics.median(stakes) if stakes else None,
        largest_stake=max(stakes, default=None),
        smallest_stake=min(stakes, default=None),
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_pnl=_total_pnl(open_bets, realized, unrealized),
        roi=realized / at_risk if realized is not None and at_risk > 0 else None,
        average_win=_mean([b.pnl or 0.0 for b in wins]),
        average_loss=_mean([b.pnl or 0.0 for b in losses]),
        profit_factor=gross_win / gross_loss if gross_loss > 0 else None,
        best_bet=_extreme(decided, max),
        worst_bet=_extreme(decided, min),
        longest_win_streak=_longest_run(results, True),
        longest_loss_streak=_longest_run(results, False),
        current_streak=_current_streak(results),
        by_side=[_group(side, [b for b in bets if b.side == side]) for side in ("YES", "NO")],
        by_entry_price=[
            _group(
                _bucket_label(lower, upper),
                [b for b in bets if _in_bucket(b.entry_price, lower, upper)],
            )
            for lower, upper in ENTRY_PRICE_BUCKETS
        ],
        pnl_curve=_pnl_curve(settled),
        bets_per_day=_bets_per_day(bets),
        recent_settled=[_line(b) for b in reversed(settled[-recent_limit:])],
    )

"""CPMM-1 fill simulation (Manifold ``calculateCpmmShares``).

Assumption (ADR-003): the simulation ignores resting limit orders in the book,
so a real fill may differ from the simulated one.
"""

import math
from dataclasses import dataclass

from ambre.domain.models import Side


@dataclass(frozen=True)
class Fill:
    side: Side
    amount: float
    shares: float
    avg_price: float
    prob_before: float  # P(YES) of the pool before the bet, whatever the side
    prob_after: float  # P(YES) of the pool after the bet, whatever the side


def _validate_pool(pool_yes: float, pool_no: float, p: float) -> None:
    if not (pool_yes > 0 and pool_no > 0):
        raise ValueError("pools must be > 0")
    if not (0 < p < 1):
        raise ValueError("p must be in (0, 1)")


def cpmm_probability(pool_yes: float, pool_no: float, p: float) -> float:
    _validate_pool(pool_yes, pool_no, p)
    return p * pool_no / ((1 - p) * pool_yes + p * pool_no)


def _buy(
    pool_yes: float, pool_no: float, p: float, amount: float, side: Side
) -> tuple[float, float, float]:
    """Return (shares, new_pool_yes, new_pool_no) after buying ``amount`` of ``side``."""
    _validate_pool(pool_yes, pool_no, p)
    if not amount > 0:
        raise ValueError("amount must be > 0")
    y, n = pool_yes, pool_no
    # Invariant k = y^p * n^(1-p), evaluated in log space to avoid overflow.
    log_k = p * math.log(y) + (1 - p) * math.log(n)
    if side is Side.YES:
        # The surviving pool is taken directly from the invariant: deriving it as
        # y + amount - shares would lose it to cancellation when it is tiny.
        new_yes = math.exp((log_k + (p - 1) * math.log(amount + n)) / p)
        return y + amount - new_yes, new_yes, n + amount
    new_no = math.exp((log_k - p * math.log(amount + y)) / (1 - p))
    return n + amount - new_no, y + amount, new_no


def cpmm_shares(pool_yes: float, pool_no: float, p: float, amount: float, side: Side) -> float:
    return _buy(pool_yes, pool_no, p, amount, side)[0]


def cpmm_fill(pool_yes: float, pool_no: float, p: float, amount: float, side: Side) -> Fill:
    shares, new_yes, new_no = _buy(pool_yes, pool_no, p, amount, side)
    return Fill(
        side=side,
        amount=amount,
        shares=shares,
        avg_price=amount / shares,
        prob_before=cpmm_probability(pool_yes, pool_no, p),
        prob_after=cpmm_probability(new_yes, new_no, p),
    )

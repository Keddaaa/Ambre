"""Expected value and Kelly sizing helpers (pure functions)."""

from dataclasses import dataclass

from ambre.domain.models import Side
from ambre.value_engine.cpmm import Fill


def ev_ideal(p: float, q: float, side: Side) -> float:
    """Edge per share at market price ``q`` for true probability ``p`` (no impact)."""
    return p - q if side is Side.YES else q - p


def kelly_yes(p: float, q: float) -> float:
    return max(0.0, (p - q) / (1 - q))


def kelly_no(p: float, q: float) -> float:
    return max(0.0, (q - p) / q)


@dataclass(frozen=True)
class FillValue:
    fill: Fill
    cost: float
    expected_payout: float
    expected_value: float
    ev_per_mana: float


def assess_fill(p_side: float, fill: Fill, fee_rate: float) -> FillValue:
    """Value a simulated fill. ``p_side`` is the probability of the purchased outcome."""
    cost = fill.amount * (1 + fee_rate)
    expected_payout = p_side * fill.shares
    expected_value = expected_payout - cost
    return FillValue(
        fill=fill,
        cost=cost,
        expected_payout=expected_payout,
        expected_value=expected_value,
        ev_per_mana=expected_value / cost,
    )

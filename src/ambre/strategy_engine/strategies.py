"""Trading strategies: turn an estimate into a pre-risk proposal (pure, no I/O)."""

from typing import Protocol

from ambre.config.settings import RiskConfig
from ambre.domain.models import (
    BinaryMarket,
    Estimate,
    MarketSnapshot,
    Proposal,
    Side,
    StrategyName,
)
from ambre.value_engine.value import kelly_no, kelly_yes

P_CONS_MIN = 0.001
P_CONS_MAX = 0.999


class Strategy(Protocol):
    name: StrategyName

    def evaluate(
        self,
        market: BinaryMarket,
        snap: MarketSnapshot,
        estimate: Estimate,
        total_value: float,
        risk: RiskConfig,
    ) -> Proposal | None: ...


class ValueHunter:
    """Bets when the uncertainty-penalised estimate still beats the market price.

    The stake is fractional Kelly on the conservative probability; it is a
    pre-risk proposal that the risk engine caps afterwards.
    """

    name = StrategyName.VALUE_HUNTER

    def evaluate(
        self,
        market: BinaryMarket,
        snap: MarketSnapshot,
        estimate: Estimate,
        total_value: float,
        risk: RiskConfig,
    ) -> Proposal | None:
        q = snap.probability
        if q <= 0 or q >= 1:
            return None  # degenerate price: Kelly is undefined
        p, u = estimate.probability, estimate.uncertainty
        if p > q:
            side = Side.YES
            p_cons = min(max(p - u, P_CONS_MIN), P_CONS_MAX)
            if p_cons <= q:
                return None
            kelly = kelly_yes(p_cons, q)
        elif p < q:
            side = Side.NO
            p_cons = min(max(p + u, P_CONS_MIN), P_CONS_MAX)
            if p_cons >= q:
                return None
            kelly = kelly_no(p_cons, q)
        else:
            return None
        stake = risk.kelly_fraction * kelly * total_value
        if stake <= 0:
            return None
        return Proposal(
            market_id=snap.market_id,
            side=side,
            stake=stake,
            conservative_probability=p_cons,
            estimate=estimate,
            reasons=[
                f"p={p:g}",
                f"q={q:g}",
                f"u={u:g}",
                f"side={side.value}",
                f"p_cons={p_cons:g}",
                f"kelly={kelly:g} x fraction {risk.kelly_fraction:g}",
            ],
            invalidation=["market moves past p_cons", "estimate uncertainty exceeds edge"],
        )


class NoTrade:
    """Explicit baseline that never trades."""

    name = StrategyName.NO_TRADE

    def evaluate(
        self,
        market: BinaryMarket,
        snap: MarketSnapshot,
        estimate: Estimate,
        total_value: float,
        risk: RiskConfig,
    ) -> Proposal | None:
        return None

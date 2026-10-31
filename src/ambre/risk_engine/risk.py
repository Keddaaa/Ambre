"""Risk engine: caps and vetoes strategy proposals (pure, no I/O)."""

from dataclasses import dataclass

from ambre.config.settings import RiskConfig
from ambre.domain.models import MarketSnapshot, Proposal, RiskVerdict, Side, SurvivalAssessment
from ambre.value_engine.cpmm import cpmm_fill
from ambre.value_engine.value import FillValue, assess_fill


@dataclass(frozen=True)
class PortfolioView:
    cash: float
    total_value: float
    open_exposure: float  # sum of the cost of OPEN positions (capital at risk)
    open_market_ids: frozenset[str]
    exposure_known: bool


class RiskEngine:
    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        proposal: Proposal,
        snap: MarketSnapshot,
        portfolio: PortfolioView,
        survival: SurvivalAssessment,
    ) -> tuple[RiskVerdict, FillValue | None]:
        """Return the verdict and the simulated fill value.

        The ``FillValue`` is returned whenever a fill was simulated (also when the
        edge after impact is too small, for the record); only
        ``verdict.approved`` authorises a trade. A rejected verdict has stake 0.
        """
        cfg = self.cfg
        reasons: list[str] = []
        if not survival.allow_new_positions:
            reasons.append(f"survival state {survival.state.value} forbids new positions")
        if not portfolio.exposure_known:
            reasons.append("exposure unknown")
        if proposal.market_id in portfolio.open_market_ids:
            reasons.append(f"market {proposal.market_id} already has an open position")
        if reasons:
            return _reject(reasons), None

        v = portfolio.total_value
        stake = min(
            proposal.stake * survival.stake_multiplier,
            cfg.max_position_fraction * v,
            portfolio.cash,
            cfg.max_total_exposure_fraction * v - portfolio.open_exposure,
        )
        if not stake >= cfg.min_stake:  # also rejects NaN
            return _reject([f"stake {stake:.4f} below min stake {cfg.min_stake:g}"]), None
        if stake < proposal.stake:
            reasons.append(f"stake capped from {proposal.stake:.4f} to {stake:.4f}")

        try:
            fill = cpmm_fill(snap.pool_yes, snap.pool_no, snap.p, stake, proposal.side)
        except ValueError as exc:
            return _reject([*reasons, f"fill simulation failed: {exc}"]), None
        p_cons = proposal.conservative_probability
        p_side = p_cons if proposal.side is Side.YES else 1 - p_cons
        fv = assess_fill(p_side, fill, cfg.fee_rate)
        if not fv.ev_per_mana >= cfg.minimum_edge:
            edge = f"edge after impact {fv.ev_per_mana:.4f} < {cfg.minimum_edge:g}"
            return _reject([*reasons, edge]), fv
        reasons.append(f"edge after impact {fv.ev_per_mana:.4f} >= {cfg.minimum_edge:g}")
        return RiskVerdict(approved=True, stake=stake, reasons=reasons), fv


def _reject(reasons: list[str]) -> RiskVerdict:
    return RiskVerdict(approved=False, stake=0.0, reasons=reasons)

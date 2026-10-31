"""Market eligibility rules (pure, no I/O)."""

from datetime import datetime

from ambre.config.settings import EligibilityConfig
from ambre.domain.models import BinaryMarket, MarketSnapshot


def check_eligibility(
    market: BinaryMarket, snap: MarketSnapshot, now: datetime, cfg: EligibilityConfig
) -> list[str]:
    """Return every reason the market cannot be traded now (empty list = eligible)."""
    reasons: list[str] = []
    if market.is_resolved:
        reasons.append(f"market resolved ({market.resolution})")
    if market.close_at is None:
        reasons.append("close time unknown (horizon unknown)")
    else:
        remaining = (market.close_at - now).total_seconds()
        if remaining <= 0:
            reasons.append("market closed")
        elif remaining < cfg.min_seconds_to_close:
            reasons.append(f"closes in {remaining:.0f}s < min {cfg.min_seconds_to_close}s")
    if market.token == "CASH":
        reasons.append("CASH market excluded")
    if snap.total_liquidity is None:
        reasons.append("liquidity unknown")
    elif snap.total_liquidity < cfg.min_total_liquidity:
        reasons.append(f"liquidity {snap.total_liquidity:g} < min {cfg.min_total_liquidity:g}")
    if snap.unique_bettor_count is None:
        reasons.append("bettor count unknown")
    elif snap.unique_bettor_count < cfg.min_unique_bettors:
        reasons.append(f"bettors {snap.unique_bettor_count} < min {cfg.min_unique_bettors}")
    return reasons

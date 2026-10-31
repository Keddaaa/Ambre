"""Point-in-time feature extraction (pure, no I/O)."""

from dataclasses import dataclass
from datetime import datetime

from ambre.domain.models import BinaryMarket, MarketSnapshot


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str
    definition: str
    source: str
    missing_policy: str


_KEEP_NONE = "kept as None, never replaced by 0"

FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        "market_probability", "float", "CPMM P(YES) at observed_at", "snapshot", "required"
    ),
    FeatureSpec("total_liquidity", "float", "Manifold totalLiquidity", "snapshot", _KEEP_NONE),
    FeatureSpec("volume_24h", "float", "Manifold volume24Hours", "snapshot", _KEEP_NONE),
    FeatureSpec("unique_bettor_count", "int", "Manifold uniqueBettorCount", "snapshot", _KEEP_NONE),
    FeatureSpec(
        "seconds_to_close",
        "float",
        "close_at - observed_at, in seconds",
        "market+snapshot",
        "None when close_at is None",
    ),
    FeatureSpec(
        "seconds_since_creation",
        "float",
        "observed_at - created_at, in seconds",
        "market+snapshot",
        "required",
    ),
)


@dataclass(frozen=True)
class FeatureVector:
    market_id: str
    available_at: datetime
    values: dict[str, float | int | None]


def extract_features(market: BinaryMarket, snap: MarketSnapshot, now: datetime) -> FeatureVector:
    """Build the feature vector known at ``snap.observed_at``.

    Durations are measured from ``snap.observed_at`` (not ``now``) so the vector
    only contains what was knowable when the snapshot was taken.
    Raises ``ValueError`` if the snapshot is from the future (look-ahead leak) or
    belongs to another market.
    """
    if snap.observed_at > now:
        raise ValueError(f"snapshot observed_at {snap.observed_at} is after now {now}")
    if snap.market_id != market.id:
        raise ValueError(f"snapshot market {snap.market_id!r} != market {market.id!r}")
    at = snap.observed_at
    seconds_to_close = (market.close_at - at).total_seconds() if market.close_at else None
    return FeatureVector(
        market_id=market.id,
        available_at=at,
        values={
            "market_probability": snap.probability,
            "total_liquidity": snap.total_liquidity,
            "volume_24h": snap.volume_24h,
            "unique_bettor_count": snap.unique_bettor_count,
            "seconds_to_close": seconds_to_close,
            "seconds_since_creation": (at - market.created_at).total_seconds(),
        },
    )

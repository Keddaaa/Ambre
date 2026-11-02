"""Map raw Manifold market payloads onto domain models."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from ambre.domain.models import BinaryMarket, MarketSnapshot


@dataclass(frozen=True)
class MappedMarket:
    market: BinaryMarket
    snapshot: MarketSnapshot


@dataclass(frozen=True)
class Rejected:
    market_id: str | None
    reason: str
    outcome_type: str | None
    # "unsupported": a well-formed market outside v0.1 scope (outcomeType, mechanism, token).
    # "invalid": missing or malformed data.
    kind: Literal["unsupported", "invalid"]


class _Invalid(Exception):
    pass


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _Invalid(f"invalid {name}: {value!r}")
    return float(value)


def _required_number(raw: dict[str, Any], key: str, name: str | None = None) -> float:
    label = name or key
    if raw.get(key) is None:
        raise _Invalid(f"missing required field: {label}")
    return _number(raw[key], label)


def _optional_number(raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    return None if value is None else _number(value, key)


def _ms_to_dt(value: float) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise _Invalid(f"timestamp out of range: {value!r}") from exc


def _optional_dt(raw: dict[str, Any], key: str) -> datetime | None:
    value = _optional_number(raw, key)
    return None if value is None else _ms_to_dt(value)


def _optional_close_dt(raw: dict[str, Any]) -> datetime | None:
    """``closeTime`` beyond the representable range (year 9999) is Manifold's "never closes"."""
    value = _optional_number(raw, "closeTime")
    if value is None:
        return None
    try:
        return _ms_to_dt(value)
    except _Invalid:
        return None


def _optional_int(raw: dict[str, Any], key: str) -> int | None:
    value = _optional_number(raw, key)
    return None if value is None else int(value)


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid(f"invalid {key}: {value!r}")
    return value


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = _optional_str(raw, key)
    if value is None:
        raise _Invalid(f"missing required field: {key}")
    return value


def _build(raw: dict[str, Any], observed_at: datetime, source_id: str) -> MappedMarket:
    market_id = _required_str(raw, "id")
    question = _required_str(raw, "question")
    created_at = _ms_to_dt(_required_number(raw, "createdTime"))
    pool = raw.get("pool")
    if not isinstance(pool, dict):
        raise _Invalid("missing required field: pool")
    pool_yes = _required_number(pool, "YES", "pool.YES")
    pool_no = _required_number(pool, "NO", "pool.NO")
    p = _required_number(raw, "p")
    probability = _required_number(raw, "probability")

    group_slugs = raw.get("groupSlugs")
    if group_slugs is not None and not (
        isinstance(group_slugs, list) and all(isinstance(g, str) for g in group_slugs)
    ):
        raise _Invalid("invalid groupSlugs")

    market = BinaryMarket(
        id=market_id,
        question=question,
        slug=_required_str(raw, "slug"),
        url=_required_str(raw, "url"),
        creator_id=_required_str(raw, "creatorId"),
        creator_username=_required_str(raw, "creatorUsername"),
        mechanism=_required_str(raw, "mechanism"),
        created_at=created_at,
        close_at=_optional_close_dt(raw),
        is_resolved=raw.get("isResolved") is True,
        resolution=_optional_str(raw, "resolution"),
        resolution_probability=_optional_number(raw, "resolutionProbability"),
        resolution_at=_optional_dt(raw, "resolutionTime"),
        token=_optional_str(raw, "token"),
        group_slugs=list(group_slugs or []),
    )
    snapshot = MarketSnapshot(
        market_id=market_id,
        probability=probability,
        pool_yes=pool_yes,
        pool_no=pool_no,
        p=p,
        total_liquidity=_optional_number(raw, "totalLiquidity"),
        volume=_optional_number(raw, "volume"),
        volume_24h=_optional_number(raw, "volume24Hours"),
        unique_bettor_count=_optional_int(raw, "uniqueBettorCount"),
        observed_at=observed_at,
        event_at=_optional_dt(raw, "lastUpdatedTime"),
        source_id=source_id,
    )
    return MappedMarket(market=market, snapshot=snapshot)


def map_market(
    raw: dict[str, Any], observed_at: datetime, source_id: str
) -> MappedMarket | Rejected:
    """Map one raw market; never raises on bad data, returns `Rejected` with a reason."""
    raw_id = raw.get("id")
    market_id = raw_id if isinstance(raw_id, str) else None
    raw_type = raw.get("outcomeType")
    outcome_type = raw_type if isinstance(raw_type, str) else None

    def reject(kind: Literal["unsupported", "invalid"], reason: str) -> Rejected:
        return Rejected(market_id=market_id, reason=reason, outcome_type=outcome_type, kind=kind)

    if outcome_type is None:
        return reject("invalid", f"invalid outcomeType: {raw_type!r}")
    if outcome_type != "BINARY":
        return reject("unsupported", f"unsupported outcomeType: {raw_type}")
    mechanism = raw.get("mechanism")
    if mechanism is None:
        return reject("invalid", "missing required field: mechanism")
    if mechanism != "cpmm-1":
        return reject("unsupported", f"unsupported mechanism: {mechanism}")
    if raw.get("token") == "CASH":
        return reject("unsupported", "unsupported token: CASH")
    try:
        return _build(raw, observed_at, source_id)
    except _Invalid as exc:
        return reject("invalid", str(exc))
    except ValidationError as exc:
        return reject("invalid", f"validation error: {exc.errors(include_url=False)}")

"""Budget Controller: strict rolling quotas, a priority reserve and pauses (pure).

State is injected (the usage timestamps and the pause end), so the decision is a pure
function that tests can drive with a fixed clock. Nothing here makes a call or spends
money: it only says whether a call is allowed now and, if not, how long to wait.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from ambre.intelligence.models import RateLimits

WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("minute", "per_minute", 60),
    ("hour", "per_hour", 3600),
    ("day", "per_day", 86400),
)


class Priority(StrEnum):
    PRIMARY = "primary"  # may use the whole quota
    SECONDARY = "secondary"  # must leave the reserve for primary work


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    wait_seconds: float | None = None
    remaining: dict[str, int | None] = field(default_factory=dict)


def decide(
    limits: RateLimits,
    usage: Sequence[datetime],
    now: datetime,
    *,
    priority: Priority,
    reserve_fraction: float,
    paused_until: datetime | None = None,
) -> BudgetDecision:
    """Allow or refuse one call of cost 1 at ``now``.

    A provider with no internal budget at all is never called: an unknown limit is not an
    unlimited one. Secondary work is capped at ``floor(cap * (1 - reserve_fraction))``.
    """
    if paused_until is not None and paused_until > now:
        wait = (paused_until - now).total_seconds()
        return BudgetDecision(False, f"provider paused for {wait:.0f}s", wait)
    if limits.is_empty:
        return BudgetDecision(False, "no internal budget defined for this provider")

    times = sorted(usage)
    remaining: dict[str, int | None] = {}
    refusals: list[tuple[float, str]] = []

    if limits.min_interval_seconds is not None and times:
        since_last = (now - times[-1]).total_seconds()
        if since_last < limits.min_interval_seconds:
            wait = limits.min_interval_seconds - since_last
            refusals.append((wait, f"min interval {limits.min_interval_seconds:g}s not elapsed"))

    for name, attr, seconds in WINDOWS:
        cap: int | None = getattr(limits, attr)
        if cap is None:
            remaining[name] = None
            continue
        start = now - timedelta(seconds=seconds)
        in_window = [t for t in times if t > start]
        used = len(in_window)
        remaining[name] = max(0, cap - used)
        effective = (
            cap if priority is Priority.PRIMARY else math.floor(cap * (1 - reserve_fraction))
        )
        if used >= effective:
            if used > effective - 1 and used - effective < used:
                wait = max(0.0, (in_window[used - effective] - start).total_seconds())
            else:  # the reserve leaves no room at all for secondary work: waiting cannot help
                wait = 0.0
            why = (
                f"{name} quota used ({used}/{cap})"
                if used >= cap
                else f"{name} quota reserved for priority work ({used}/{effective} of {cap})"
            )
            refusals.append((wait, why))

    if refusals:
        wait, why = max(refusals, key=lambda r: r[0])
        return BudgetDecision(False, why, wait if wait > 0 else None, remaining)
    return BudgetDecision(True, "within budget", None, remaining)


def backoff_seconds(
    failures: int, *, base: float, cap: float, retry_after: float | None = None
) -> float:
    """Pause after a failure: ``base * 2**failures`` up to ``cap``, never below the provider's
    own ``Retry-After``."""
    exponential = min(base * float(2 ** max(0, failures)), cap)
    return max(exponential, retry_after) if retry_after is not None else exponential

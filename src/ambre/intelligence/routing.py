"""Smart Source Router (pure): pick the best eligible provider for an information need and
explain why each other provider was not picked.

Version 1 is deterministic: eligible providers are ranked by their a-priori relevance for the
need (registry), ties broken by provider id. Measured contribution replaces the prior only in
the learning phase.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from ambre.intelligence.budget import BudgetDecision
from ambre.intelligence.models import InfoNeed, ProviderCard


@dataclass(frozen=True)
class ProviderHealth:
    paused_until: datetime | None = None
    consecutive_failures: int = 0


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    eligible: bool
    relevance_prior: float | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RoutingDecision:
    need: InfoNeed
    chosen: str | None
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    explanation: str = ""


def route(
    need: InfoNeed,
    cards: Iterable[ProviderCard],
    *,
    health: Mapping[str, ProviderHealth],
    budgets: Mapping[str, BudgetDecision],
    now: datetime | None = None,
) -> RoutingDecision:
    candidates: list[Candidate] = []
    for card in cards:
        prior = card.capabilities.get(need)
        reasons: list[str] = []
        if not card.provides(need):
            reasons.append(f"does not provide {need.value}")
        if not card.enabled:
            reasons.append("disabled in the registry")
        h = health.get(card.id)
        if h is not None and h.paused_until is not None and (now is None or h.paused_until > now):
            reasons.append(f"paused until {h.paused_until.isoformat()}")
        if card.provides(need) and card.enabled:
            budget = budgets.get(card.id)
            if budget is None:
                reasons.append("no budget decision available")
            elif not budget.allowed:
                reasons.append(f"budget: {budget.reason}")
        candidates.append(
            Candidate(card.id, eligible=not reasons, relevance_prior=prior, reasons=tuple(reasons))
        )

    eligible = sorted(
        (c for c in candidates if c.eligible),
        key=lambda c: (-(c.relevance_prior or 0.0), c.provider_id),
    )
    chosen = eligible[0] if eligible else None
    final: list[Candidate] = []
    for c in candidates:
        if c.eligible and chosen is not None and c.provider_id != chosen.provider_id:
            c = Candidate(
                c.provider_id,
                False,
                c.relevance_prior,
                (
                    f"eligible but lower relevance prior ({c.relevance_prior:g}) "
                    f"than {chosen.provider_id}",
                ),
            )
        final.append(c)
    final.sort(key=lambda c: c.provider_id)

    if chosen is None:
        lines = [f"no eligible provider for {need.value}"]
    else:
        lines = [
            f"chose {chosen.provider_id} for {need.value} "
            f"(relevance prior {chosen.relevance_prior:g})"
        ]
    lines += [f"- {c.provider_id}: {'; '.join(c.reasons)}" for c in final if c.reasons]
    return RoutingDecision(
        need=need,
        chosen=chosen.provider_id if chosen else None,
        candidates=tuple(final),
        explanation="\n".join(lines),
    )

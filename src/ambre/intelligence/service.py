"""Research pass: plan, route, budget, fetch, normalise, store, link and explain.

One pass looks at the open markets, finds what they lack (Information Planner), groups the
needs so that several markets share a single request, and for each group decides whether a
search is worth its quota. Every decision, including "do not search", is written to the
Research Memory with its explanation.

This module never touches decisions, positions or the ledger: the network informs, the
quantitative engines and the Risk Engine decide.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ambre.config.settings import Settings
from ambre.infrastructure import repositories as repo
from ambre.intelligence import store
from ambre.intelligence.budget import BudgetDecision, Priority, backoff_seconds, decide
from ambre.intelligence.matching import match_observation
from ambre.intelligence.models import (
    FetchResult,
    InfoNeed,
    Observation,
    ProviderCard,
    ResultKind,
)
from ambre.intelligence.orm import IntelRequestRow
from ambre.intelligence.planning import Gap, Gazetteer, plan_gaps
from ambre.intelligence.providers.base import Provider
from ambre.intelligence.registry import Registry
from ambre.intelligence.routing import ProviderHealth, route
from ambre.intelligence.value import estimate_value
from ambre.pipeline import to_market, to_snapshot

logger = logging.getLogger(__name__)

SKIPPED_FRESH = "SKIPPED_FRESH"
SKIPPED_LOW_VALUE = "SKIPPED_LOW_VALUE"
SKIPPED_NO_PROVIDER = "SKIPPED_NO_PROVIDER"
SKIPPED_NO_CONNECTOR = "SKIPPED_NO_CONNECTOR"
CACHE_STATUSES = (ResultKind.OK.value.upper(), ResultKind.EMPTY.value.upper())


@dataclass
class ResearchReport:
    requests: list[dict[str, Any]] = field(default_factory=list)
    new_observations: int = 0
    new_links: int = 0
    calls: int = 0
    #: markets whose information need was NOT satisfied this pass (skipped or failed). A market
    #: is never considered informed by a search that did not happen or did not succeed.
    markets_without_information: list[str] = field(default_factory=list)


def _group_gaps(
    gaps: list[tuple[Gap, float, datetime]],
) -> dict[InfoNeed, list[tuple[Gap, float, datetime]]]:
    groups: dict[InfoNeed, list[tuple[Gap, float, datetime]]] = defaultdict(list)
    for item in gaps:
        groups[item[0].need].append(item)
    return dict(groups)


def _earthquake_params(
    gaps: list[tuple[Gap, float, datetime]], settings: Settings, now: datetime
) -> dict[str, Any]:
    cfg = settings.intelligence
    thresholds = [
        g.min_magnitude if g.min_magnitude is not None else cfg.earthquake_floor_magnitude
        for g, _, _ in gaps
    ]
    return {
        "min_magnitude": min(thresholds),
        "since": now - timedelta(days=cfg.lookback_days),
        "limit": cfg.fetch_limit,
    }


async def _fetch(provider: Provider, need: InfoNeed, params: dict[str, Any]) -> FetchResult:
    if need is InfoNeed.RECENT_EARTHQUAKES:
        return await provider.fetch_latest(**params)
    return FetchResult(kind=ResultKind.UNSUPPORTED, detail=f"no fetch routine for {need.value}")


def _status_for(result: FetchResult) -> str:
    return result.kind.value.upper()


def _budget_for(
    session: Session, card: ProviderCard, now: datetime, paused_until: datetime | None
) -> BudgetDecision:
    usage = store.usage_since(session, card.id, now - timedelta(days=1))
    return decide(
        card.internal_budget,
        usage,
        now,
        priority=Priority.SECONDARY,
        reserve_fraction=card.reserve_fraction,
        paused_until=paused_until,
    )


async def research_pass(
    session: Session,
    settings: Settings,
    now: datetime,
    *,
    registry: Registry,
    providers: Mapping[str, Provider],
    gazetteer: Gazetteer,
) -> ResearchReport:
    """Run one research pass at ``now``. The caller commits."""
    report = ResearchReport()
    pairs = repo.latest_snapshots_for_open_markets(
        session, settings.intelligence.max_markets_scanned
    )
    gaps: list[tuple[Gap, float, datetime]] = []
    for market_row, snap_row in pairs:
        market, snap = to_market(market_row), to_snapshot(snap_row)
        gap = plan_gaps(market.id, market.question, market.close_at, now, gazetteer)
        if gap is not None:
            gaps.append((gap, snap.probability, market.created_at))

    for need, group in _group_gaps(gaps).items():
        entry = await _research_group(
            session, settings, now, registry, providers, need, group, report
        )
        report.requests.append(entry)
    return report


async def _research_group(
    session: Session,
    settings: Settings,
    now: datetime,
    registry: Registry,
    providers: Mapping[str, Provider],
    need: InfoNeed,
    group: list[tuple[Gap, float, datetime]],
    report: ResearchReport,
) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    market_ids = sorted({g.market_id for g, _, _ in group})
    params = _earthquake_params(group, settings, now)
    explanation: dict[str, Any] = {
        "why": [{"market_id": g.market_id, "objective": g.objective, "q": q} for g, q, _ in group],
        "unresolved": sorted({u for g, _, _ in group for u in g.unresolved}),
        "markets": market_ids,
        "usefulness_note": "not assessed: measured in the learning phase, never assumed",
    }

    cards = registry.all()
    health: dict[str, ProviderHealth] = {}
    budgets: dict[str, BudgetDecision] = {}
    for card in cards:
        state = store.get_state(session, card.id)
        paused = state.paused_until if state is not None else None
        health[card.id] = ProviderHealth(
            paused_until=paused, consecutive_failures=state.consecutive_failures if state else 0
        )
        if card.enabled and card.provides(need):
            budgets[card.id] = _budget_for(session, card, now, paused)
    routing = route(need, cards, health=health, budgets=budgets, now=now)
    explanation["routing"] = {
        "chosen": routing.chosen,
        "text": routing.explanation,
        "candidates": [
            {
                "provider": c.provider_id,
                "eligible": c.eligible,
                "prior": c.relevance_prior,
                "reasons": list(c.reasons),
            }
            for c in routing.candidates
        ],
    }

    def finish(
        status: str,
        *,
        provider_id: str | None,
        value: float | None,
        units: int,
        count: int | None = None,
        invalid: int | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        session.add(
            IntelRequestRow(
                id=request_id,
                created_at=now,
                need=need.value,
                status=status,
                provider_id=provider_id,
                market_ids=market_ids,
                params={
                    k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in params.items()
                },
                value_score=value,
                cost_units=units,
                observation_count=count,
                invalid_items=invalid,
                detail=detail,
                explanation=explanation,
                usefulness=None,
            )
        )
        if status not in ("OK", "EMPTY"):
            report.markets_without_information.extend(market_ids)
        return {
            "request_id": request_id,
            "status": status,
            "provider": provider_id,
            "markets": market_ids,
            "value_score": value,
            "detail": detail,
            "explanation": explanation,
        }

    # A valid answer already in memory beats any budget or routing question: nothing is needed.
    for card in cards:
        if not (card.enabled and card.provides(need) and card.freshness_seconds is not None):
            continue
        known = store.last_request(session, need.value, card.id, CACHE_STATUSES)
        if known is None:
            continue
        age = (now - known.created_at).total_seconds()
        if age < card.freshness_seconds:
            explanation["stop"] = (
                f"already known: {card.id} answered {age:.0f}s ago, valid for "
                f"{card.freshness_seconds}s"
            )
            return finish(
                SKIPPED_FRESH, provider_id=card.id, value=None, units=0, detail=explanation["stop"]
            )

    if routing.chosen is None:
        return finish(
            SKIPPED_NO_PROVIDER, provider_id=None, value=None, units=0, detail=routing.explanation
        )

    chosen_card = registry.get(routing.chosen)
    assert chosen_card is not None
    card_id = chosen_card.id
    prior = chosen_card.capabilities[need]
    last = store.last_request(session, need.value, card_id, CACHE_STATUSES)
    cache_age = (now - last.created_at).total_seconds() if last is not None else None
    scored = [
        estimate_value(
            market_probability=q,
            relevance_prior=prior,
            cache_age_seconds=cache_age,
            freshness_seconds=chosen_card.freshness_seconds,
        )
        for _, q, _ in group
    ]
    best = max(scored, key=lambda e: e.score)
    threshold = settings.intelligence.min_value_score
    explanation["value"] = {
        "score": best.score,
        "components": best.components,
        "threshold": threshold,
        "expected_information_gain": None,
        "cache_age_seconds": cache_age,
        "kind": (
            "ranking score (prior x market uncertainty x staleness / cost), not a measured gain"
        ),
    }
    explanation["budget"] = {
        "provider": card_id,
        "reason": budgets[card_id].reason,
        "remaining": budgets[card_id].remaining,
    }
    explanation["cost"] = {"provider": card_id, "units": 1, "unit": "request", "money": 0}

    if (
        cache_age is not None
        and chosen_card.freshness_seconds is not None
        and cache_age < chosen_card.freshness_seconds
    ):
        explanation["stop"] = (
            f"already known: last answer {cache_age:.0f}s old, "
            f"valid for {chosen_card.freshness_seconds}s"
        )
        return finish(
            SKIPPED_FRESH,
            provider_id=card_id,
            value=best.score,
            units=0,
            detail=explanation["stop"],
        )
    if best.score < threshold:
        explanation["stop"] = (
            f"value {best.score:.4f} below threshold {threshold:g}: not worth the quota"
        )
        return finish(
            SKIPPED_LOW_VALUE,
            provider_id=card_id,
            value=best.score,
            units=0,
            detail=explanation["stop"],
        )
    provider = providers.get(card_id)
    if provider is None:
        explanation["stop"] = f"no connector implemented for {card_id}"
        return finish(
            SKIPPED_NO_CONNECTOR,
            provider_id=card_id,
            value=best.score,
            units=0,
            detail=explanation["stop"],
        )

    # The call is paid for (counted) before it is made, whatever its outcome.
    store.record_usage(session, card_id, now, purpose="research", request_id=request_id)
    report.calls += 1
    result = await _fetch(provider, need, params)
    explanation["result"] = {
        "kind": result.kind.value,
        "http_status": result.http_status,
        "detail": result.detail,
    }

    if result.kind in (ResultKind.OK, ResultKind.EMPTY):
        store.mark_success(session, card_id, now)
    else:
        state = store.get_state(session, card_id)
        failures = state.consecutive_failures if state is not None else 0
        cfg = settings.intelligence
        pause = backoff_seconds(
            failures,
            base=cfg.failure_backoff_base_seconds,
            cap=cfg.failure_backoff_cap_seconds,
            retry_after=result.retry_after_seconds,
        )
        if result.kind is ResultKind.FAILED:
            store.mark_failure(session, card_id, now, result.detail or "failed", pause)
            explanation["pause_seconds"] = pause
    # The request row must exist before observations reference it.
    entry = finish(
        _status_for(result),
        provider_id=card_id,
        value=best.score,
        units=1,
        count=len(result.observations),
        invalid=result.invalid_items,
        detail=result.detail,
    )
    session.flush()

    if result.kind is ResultKind.OK:
        _store_and_link(session, now, request_id, result.observations, group, report, explanation)
        entry["explanation"] = explanation
    return entry


def _store_and_link(
    session: Session,
    now: datetime,
    request_id: str,
    observations: tuple[Observation, ...],
    group: list[tuple[Gap, float, datetime]],
    report: ResearchReport,
    explanation: dict[str, Any],
) -> None:
    for obs in observations:
        if store.save_observation(session, obs, request_id):
            report.new_observations += 1
    session.flush()
    linked: dict[str, list[str]] = defaultdict(list)
    for obs in observations:
        for gap, _, created_at in group:
            link = match_observation(obs, gap, market_created_at=created_at, now=now)
            if link is not None and store.save_link(session, link, now):
                report.new_links += 1
                linked[gap.market_id].append(obs.observation_id)
    explanation["links"] = {m: ids for m, ids in linked.items()}
    row = session.get(IntelRequestRow, request_id)
    if row is not None:
        row.explanation = dict(explanation)  # reassign so the JSON change is persisted

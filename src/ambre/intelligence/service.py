"""Research pass: plan, rank, route, budget, fetch, normalise, store, link, explain, remember.

One pass looks at every open market, finds what it lacks (Universal Research Planner), groups
the needs so that markets about the same subject share one request, ranks the groups, and for
each group decides whether a search is worth its quota. Every decision that reaches a provider is
written to the Research Memory with its explanation and to the Cognitive Vault; every market gets
an information state that is recomputed after the pass and never silently disappears.

The pass is paced: specialised searches first, then the generalist ones by market uncertainty,
at most ``max_calls_per_pass`` calls, waiting briefly (never beyond ``max_wait_in_pass_seconds``)
for a provider's minimum interval. What does not fit waits for a later pass; nothing is dropped.

This module never touches decisions, positions or the ledger: the network informs, the
quantitative engines and the Risk Engine decide.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ambre.cognitive import vault as cognitive_vault
from ambre.cognitive.models import EvidenceLevel, NoteDraft, NoteType, Provenance
from ambre.config.settings import Settings
from ambre.domain.models import Actor
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.orm import MarketRow, SnapshotRow
from ambre.intelligence import store
from ambre.intelligence.budget import BudgetDecision, Priority, backoff_seconds, decide
from ambre.intelligence.context import FeatureSet, build_feature_set
from ambre.intelligence.estimation import model_available
from ambre.intelligence.generic_planning import MarketPlan, PlanStatus, plan_market
from ambre.intelligence.matching import match_forecast, match_news, match_observation
from ambre.intelligence.models import (
    FetchResult,
    InfoNeed,
    Observation,
    ProviderCard,
    ResultKind,
)
from ambre.intelligence.orm import IntelMarketLinkRow, IntelMarketStateRow, IntelRequestRow
from ambre.intelligence.planning import Gazetteer
from ambre.intelligence.providers.base import Provider
from ambre.intelligence.queries import group_search
from ambre.intelligence.registry import Registry
from ambre.intelligence.resolution import ResearchRules, analyze_market, load_rules
from ambre.intelligence.routing import ProviderHealth, route
from ambre.intelligence.states import MarketInfoState, Outcome, derive_state
from ambre.intelligence.value import estimate_value
from ambre.pipeline import to_market, to_snapshot

logger = logging.getLogger(__name__)

SKIPPED_FRESH = "SKIPPED_FRESH"
SKIPPED_LOW_VALUE = "SKIPPED_LOW_VALUE"
SKIPPED_NO_PROVIDER = "SKIPPED_NO_PROVIDER"
SKIPPED_NO_CONNECTOR = "SKIPPED_NO_CONNECTOR"
CACHE_STATUSES = (ResultKind.OK.value.upper(), ResultKind.EMPTY.value.upper())
Sleep = Callable[[float], Awaitable[None]]

_OUTCOME_OF_STATUS = {
    "OK": Outcome.OK,
    "EMPTY": Outcome.EMPTY,
    "INCONCLUSIVE": Outcome.INCONCLUSIVE,
    "FAILED": Outcome.FAILED,
    "UNSUPPORTED": Outcome.FAILED,
    SKIPPED_FRESH: Outcome.FRESH,
    SKIPPED_LOW_VALUE: Outcome.LOW_VALUE,
    SKIPPED_NO_PROVIDER: Outcome.NO_PROVIDER,
    SKIPPED_NO_CONNECTOR: Outcome.NO_CONNECTOR,
}
#: Outcomes that say nothing new about evidence already on file: old evidence stays visible.
_KEEPS_OLD_EVIDENCE = (
    Outcome.BUDGET_OR_QUEUE,
    Outcome.LOW_VALUE,
    Outcome.NO_PROVIDER,
    Outcome.INCONCLUSIVE,
    Outcome.FAILED,
)


@dataclass
class ResearchReport:
    requests: list[dict[str, Any]] = field(default_factory=list)
    new_observations: int = 0
    new_links: int = 0
    calls: int = 0
    waited_seconds: float = 0.0
    #: markets whose information need was NOT satisfied this pass (skipped or failed). A market
    #: is never considered informed by a search that did not happen or did not succeed.
    markets_without_information: list[str] = field(default_factory=list)
    #: what the planner understood about every scanned open market, by plan status and domain
    plans: dict[str, int] = field(default_factory=dict)
    domains: dict[str, int] = field(default_factory=dict)
    #: information state of every scanned market after this pass (they sum to the scan)
    states: dict[str, int] = field(default_factory=dict)
    #: groups left for a later pass because of the pass limit (no request row is written for them)
    queued_groups: int = 0
    #: why a generalist provider could not be used this pass (budget, pause), by provider
    provider_unavailable: dict[str, str] = field(default_factory=dict)
    #: markets recognised but not collectable (ambiguous or beyond the forecast horizon)
    not_collectable: list[dict[str, Any]] = field(default_factory=list)
    #: rows added to the feature journal this pass (knowledge that changed)
    journaled: int = 0


@dataclass(frozen=True)
class Item:
    """A collectable market: its plan, the market price and when it was created."""

    plan: MarketPlan
    q: float
    created_at: datetime

    @property
    def market_id(self) -> str:
        return self.plan.market_id


@dataclass
class PassClock:
    """Pass-local time: it only moves forward, by the waits the pass really did."""

    now: datetime


@dataclass
class GroupResult:
    entry: dict[str, Any] | None
    outcome: Outcome
    made_call: bool = False


def _group_key(plan: MarketPlan) -> str:
    """Markets that can share one request share a key: one earthquake feed, one place and
    variable for weather, one entity-and-domain topic for the generalist news search."""
    if plan.weather is not None:
        w = plan.weather
        return f"{w.latitude:.2f},{w.longitude:.2f}|{w.timezone}|{w.variable}"
    if plan.topic is not None:
        return f"news|{plan.topic.key}"
    return "global"


def _group_items(items: list[Item]) -> dict[tuple[InfoNeed, str], list[Item]]:
    groups: dict[tuple[InfoNeed, str], list[Item]] = defaultdict(list)
    for item in items:
        assert item.plan.need is not None
        groups[(item.plan.need, _group_key(item.plan))].append(item)
    return dict(groups)


def _uncertainty(q: float) -> float:
    return 4 * q * (1 - q) if 0.0 < q < 1.0 else 0.0


def _rank(entry: tuple[tuple[InfoNeed, str], list[Item]]) -> tuple[int, float, str]:
    (need, key), group = entry
    level = min(i.plan.level for i in group)
    return (level, -max(_uncertainty(i.q) for i in group), f"{need.value}|{key}")


def _params_for(
    need: InfoNeed, group: list[Item], settings: Settings, now: datetime
) -> dict[str, Any]:
    cfg = settings.intelligence
    if need is InfoNeed.RECENT_EARTHQUAKES:
        thresholds = [
            i.plan.gap.min_magnitude
            if i.plan.gap is not None and i.plan.gap.min_magnitude is not None
            else cfg.earthquake_floor_magnitude
            for i in group
        ]
        return {
            "min_magnitude": min(thresholds),
            "since": now - timedelta(days=cfg.lookback_days),
            "limit": cfg.fetch_limit,
        }
    if need is InfoNeed.GENERAL_NEWS:
        topics = [i.plan.topic for i in group if i.plan.topic is not None]
        return {
            "query": group_search(topics),
            "timespan": cfg.news_timespan,
            "max_records": cfg.news_max_records,
        }
    first = group[0].plan.weather
    assert first is not None
    days = max(
        ((i.plan.weather.target_date - now.date()).days for i in group if i.plan.weather), default=0
    )
    return {
        "latitude": first.latitude,
        "longitude": first.longitude,
        "timezone": first.timezone,
        "variable": first.variable,
        "forecast_days": days + 1,
    }


def _objective(item: Item) -> str:
    plan = item.plan
    if plan.gap is not None:
        return plan.gap.objective
    if plan.topic is not None:
        subject = plan.topic.primary or " ".join(plan.topic.terms)
        return f"find reports about {subject} that bear on this market (generalist search)"
    w = plan.weather
    assert w is not None
    return f"collect the {w.variable} forecast for {w.location_name} on {w.target_date.isoformat()}"


def _unresolved(item: Item) -> list[str]:
    plan = item.plan
    base = list(plan.gap.unresolved) if plan.gap is not None else []
    unqueried = [
        f"named source {s} is not queried directly (no connector): news only"
        for s in plan.analysis.reference_sources
        if plan.level == 2
    ]
    return sorted({*base, *plan.blocking, *plan.analysis.missing, *unqueried})


async def _fetch(provider: Provider, need: InfoNeed, params: dict[str, Any]) -> FetchResult:
    call = {
        k: v for k, v in params.items() if k != "group_key"
    }  # bookkeeping, not an API parameter
    if need in (InfoNeed.RECENT_EARTHQUAKES, InfoNeed.WEATHER_FORECAST):
        return await provider.fetch_latest(**call)
    if need is InfoNeed.GENERAL_NEWS:
        return await provider.search(call.pop("query"), **call)
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
    rules: ResearchRules | None = None,
    sleep: Sleep | None = None,
) -> ResearchReport:
    """Run one research pass at ``now``. The caller commits."""
    report = ResearchReport()
    rules = rules or load_rules()
    cfg = settings.intelligence
    pairs = repo.latest_snapshots_for_open_markets(session, cfg.max_markets_scanned)
    plans: dict[str, MarketPlan] = {}
    items: list[Item] = []
    for market_row, snap_row in pairs:
        market, snap = to_market(market_row), to_snapshot(snap_row)
        analysis = analyze_market(
            market.id, market.question, market.close_at, now, gazetteer, rules
        )
        plan = plan_market(analysis, gazetteer, now)
        plans[market.id] = plan
        report.plans[plan.status.value] = report.plans.get(plan.status.value, 0) + 1
        report.domains[plan.domain.value] = report.domains.get(plan.domain.value, 0) + 1
        if plan.status is PlanStatus.COLLECTABLE:
            items.append(Item(plan, snap.probability, market.created_at))
        elif (
            plan.status in (PlanStatus.AMBIGUOUS, PlanStatus.OUT_OF_HORIZON)
            and len(report.not_collectable) < 50
        ):
            report.not_collectable.append(
                {
                    "market_id": plan.market_id,
                    "domain": plan.domain.value,
                    "status": plan.status.value,
                    "blocking": list(plan.blocking),
                }
            )

    clock = PassClock(now)
    outcomes: dict[str, Outcome] = {}
    ordered = sorted(_group_items(items).items(), key=_rank)
    calls_made = 0
    for rank, ((need, key), group) in enumerate(ordered, start=1):
        ids = sorted({i.market_id for i in group})
        if calls_made >= cfg.max_calls_per_pass:
            report.queued_groups += 1
            for market_id in ids:
                outcomes[market_id] = Outcome.BUDGET_OR_QUEUE
            continue
        result = await _research_group(
            session, settings, clock, registry, providers, need, key, group, report,
            queue=(rank, len(ordered)), sleep=sleep,
        )  # fmt: skip
        if result.entry is not None:
            report.requests.append(result.entry)
        if result.made_call:
            calls_made += 1
        for market_id in ids:
            outcomes[market_id] = result.outcome

    _write_states(session, plans, outcomes, registry, clock.now, report)
    report.journaled = _journal_features(session, pairs, plans, registry, clock.now)
    return report


def _journal_features(
    session: Session,
    pairs: Sequence[tuple[MarketRow, SnapshotRow]],
    plans: dict[str, MarketPlan],
    registry: Registry,
    now: datetime,
) -> int:
    """Record the dated features of every market that has some. Reads only; bets nothing."""
    written = 0
    for market_row, snap_row in pairs:
        plan = plans[market_row.id]
        if plan.status is not PlanStatus.COLLECTABLE:
            continue
        features = market_context(
            session, plan, registry, now, market_created_at=market_row.created_at
        )
        if store.append_feature_journal(session, plan, features, now, snap_row.probability):
            written += 1
    return written


def _searchable(registry: Registry, need: InfoNeed | None) -> bool:
    return need is not None and bool(registry.providers_for(need))


def _write_states(
    session: Session,
    plans: dict[str, MarketPlan],
    outcomes: dict[str, Outcome],
    registry: Registry,
    now: datetime,
    report: ResearchReport,
) -> None:
    """Recompute and store the information state of every scanned market."""
    evidence = dict(
        session.execute(
            select(IntelMarketLinkRow.market_id, func.count()).group_by(
                IntelMarketLinkRow.market_id
            )
        ).all()
    )
    existing = {r.market_id: r for r in session.scalars(select(IntelMarketStateRow))}
    for market_id, plan in plans.items():
        original = outcomes.get(market_id, Outcome.NOT_CONSIDERED)
        outcome = original
        count = int(evidence.get(market_id, 0))
        reason_suffix = ""
        if count > 0 and outcome in _KEEPS_OLD_EVIDENCE:
            reason_suffix = f" (evidence on file; this pass: {outcome.value})"
            outcome = Outcome.FRESH
        decision = derive_state(
            plan,
            outcome,
            has_evidence=count > 0,
            model_available=model_available(plan),
            searchable=_searchable(registry, plan.need),
        )
        report.states[decision.state.value] = report.states.get(decision.state.value, 0) + 1
        researched = original in (Outcome.OK, Outcome.EMPTY, Outcome.INCONCLUSIVE, Outcome.FAILED)
        detail = {
            "blocking": list(plan.blocking),
            "ambiguities": list(plan.analysis.ambiguities),
            "reasons": list(plan.reasons),
        }
        row = existing.get(market_id)
        if row is None:
            row = IntelMarketStateRow(market_id=market_id, evidence_count=0, detail={})
            session.add(row)
        row.state = decision.state.value
        row.domain = plan.domain.value
        row.plan_status = plan.status.value
        row.level = plan.level
        row.estimable = plan.estimable
        row.topic_key = plan.topic.key if plan.topic is not None else None
        row.reason = decision.reason + reason_suffix
        row.last_outcome = original.value
        row.evidence_count = count
        if researched:
            row.last_researched_at = now
        row.updated_at = now
        row.detail = detail


async def _research_group(
    session: Session,
    settings: Settings,
    clock: PassClock,
    registry: Registry,
    providers: Mapping[str, Provider],
    need: InfoNeed,
    group_key: str,
    group: list[Item],
    report: ResearchReport,
    *,
    queue: tuple[int, int],
    sleep: Sleep | None,
) -> GroupResult:
    now = clock.now
    request_id = str(uuid.uuid4())
    market_ids = sorted({i.market_id for i in group})
    params = _params_for(need, group, settings, now)
    params["group_key"] = group_key
    explanation: dict[str, Any] = {
        "why": [{"market_id": i.market_id, "objective": _objective(i), "q": i.q} for i in group],
        "plan": {
            "level": min(i.plan.level for i in group),
            "need": need.value,
            "queue_rank": queue[0],
            "queue_size": queue[1],
            "topic": group[0].plan.topic.key if group[0].plan.topic else None,
            "query": params.get("query"),
        },
        "understanding": [
            {
                "market_id": i.market_id,
                "domain": i.plan.domain.value,
                "estimable": i.plan.estimable,
                "blocking": list(i.plan.blocking),
                "yes_condition": i.plan.analysis.yes_condition,
                "no_condition": i.plan.analysis.no_condition,
                "event_period": (
                    None
                    if i.plan.analysis.period is None
                    else {
                        "kind": i.plan.analysis.period.kind.value,
                        "end": i.plan.analysis.period.end.isoformat(),
                    }
                ),
                "closes_at": (
                    i.plan.analysis.closes_at.isoformat() if i.plan.analysis.closes_at else None
                ),
                "ambiguities": list(i.plan.analysis.ambiguities),
                "reference_sources": list(i.plan.analysis.reference_sources),
                "level": i.plan.level,
            }
            for i in group
        ],
        "unresolved": sorted({u for i in group for u in _unresolved(i)}),
        "markets": market_ids,
        "usefulness_note": "not assessed: measured in the learning phase, never assumed",
    }

    cards = registry.all()

    def routing_and_budgets() -> tuple[Any, dict[str, BudgetDecision], dict[str, ProviderHealth]]:
        health: dict[str, ProviderHealth] = {}
        budgets: dict[str, BudgetDecision] = {}
        for card in cards:
            state = store.get_state(session, card.id)
            paused = state.paused_until if state is not None else None
            health[card.id] = ProviderHealth(
                paused_until=paused, consecutive_failures=state.consecutive_failures if state else 0
            )
            if card.enabled and card.provides(need):
                budgets[card.id] = _budget_for(session, card, clock.now, paused)
        return route(need, cards, health=health, budgets=budgets, now=clock.now), budgets, health

    routing, budgets, health = routing_and_budgets()
    # A provider that only asks for a short pause between calls is waited for, within a cap.
    if routing.chosen is None and sleep is not None:
        waits = [
            b.wait_seconds
            for cid, b in budgets.items()
            if not b.allowed and b.wait_seconds is not None and health[cid].paused_until is None
        ]
        cap = settings.intelligence.max_wait_in_pass_seconds
        if waits and len(waits) == len(budgets) and 0 < min(waits) <= cap:
            pause = min(waits) + 0.05
            await sleep(pause)
            clock.now = clock.now + timedelta(seconds=pause)
            report.waited_seconds += pause
            now = clock.now
            routing, budgets, health = routing_and_budgets()
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

    def done(entry: dict[str, Any], *, made_call: bool = False) -> GroupResult:
        return GroupResult(entry, _OUTCOME_OF_STATUS[entry["status"]], made_call)

    # A valid answer already in memory beats any budget or routing question: nothing is needed.
    for card in cards:
        if not (card.enabled and card.provides(need) and card.freshness_seconds is not None):
            continue
        known = store.last_request(
            session, need.value, card.id, CACHE_STATUSES, group_key=group_key
        )
        if known is None:
            continue
        age = (now - known.created_at).total_seconds()
        if age < card.freshness_seconds:
            explanation["stop"] = (
                f"already known: {card.id} answered {age:.0f}s ago, valid for "
                f"{card.freshness_seconds}s"
            )
            return done(
                finish(
                    SKIPPED_FRESH,
                    provider_id=card.id,
                    value=None,
                    units=0,
                    detail=explanation["stop"],
                )
            )

    if routing.chosen is None:
        if need is InfoNeed.GENERAL_NEWS:
            # A provider that is missing, paused or out of budget is the same for every topic:
            # one line in the report, not one request row per topic and pass. Each market keeps
            # its own state (NO_AVAILABLE_SOURCE or RESEARCH_DEFERRED) with the reason.
            for c in routing.candidates:
                candidate = registry.get(c.provider_id)
                if (
                    candidate is not None
                    and candidate.enabled
                    and candidate.provides(need)
                    and c.reasons
                ):
                    report.provider_unavailable[c.provider_id] = "; ".join(c.reasons)
            return GroupResult(None, Outcome.NO_PROVIDER)
        return done(
            finish(
                SKIPPED_NO_PROVIDER,
                provider_id=None,
                value=None,
                units=0,
                detail=routing.explanation,
            )
        )

    chosen_card = registry.get(routing.chosen)
    assert chosen_card is not None
    card_id = chosen_card.id
    prior = chosen_card.capabilities[need]
    last = store.last_request(session, need.value, card_id, CACHE_STATUSES, group_key=group_key)
    cache_age = (now - last.created_at).total_seconds() if last is not None else None
    scored = [
        estimate_value(
            market_probability=i.q,
            relevance_prior=prior,
            cache_age_seconds=cache_age,
            freshness_seconds=chosen_card.freshness_seconds,
        )
        for i in group
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

    if best.score < threshold:
        explanation["stop"] = (
            f"value {best.score:.4f} below threshold {threshold:g}: not worth the quota"
        )
        return done(
            finish(
                SKIPPED_LOW_VALUE,
                provider_id=card_id,
                value=best.score,
                units=0,
                detail=explanation["stop"],
            )
        )
    provider = providers.get(card_id)
    if provider is None:
        explanation["stop"] = f"no connector implemented for {card_id}"
        return done(
            finish(
                SKIPPED_NO_CONNECTOR,
                provider_id=card_id,
                value=best.score,
                units=0,
                detail=explanation["stop"],
            )
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
    _remember(session, entry, group, now)
    return done(entry, made_call=True)


def _store_and_link(
    session: Session,
    now: datetime,
    request_id: str,
    observations: tuple[Observation, ...],
    group: list[Item],
    report: ResearchReport,
    explanation: dict[str, Any],
) -> None:
    for obs in observations:
        if store.save_observation(session, obs, request_id):
            report.new_observations += 1
    session.flush()
    linked: dict[str, list[str]] = defaultdict(list)
    for obs in observations:
        for item in group:
            link = None
            if item.plan.gap is not None:
                link = match_observation(
                    obs, item.plan.gap, market_created_at=item.created_at, now=now
                )
            elif item.plan.weather is not None:
                link = match_forecast(obs, item.plan.weather, item.market_id, now)
            elif item.plan.topic is not None:
                link = match_news(obs, item.plan.topic, item.market_id, now)
            if link is not None and store.save_link(session, link, now):
                report.new_links += 1
                linked[item.market_id].append(obs.observation_id)
    explanation["links"] = {m: ids for m, ids in linked.items()}
    explanation["independent_sources"] = {
        m: len(
            {
                o.payload.domain  # type: ignore[union-attr]
                for o in observations
                if o.observation_id in set(ids) and getattr(o.payload, "domain", None)
            }
        )
        for m, ids in linked.items()
    }
    row = session.get(IntelRequestRow, request_id)
    if row is not None:
        row.explanation = dict(explanation)  # reassign so the JSON change is persisted


def _remember(session: Session, entry: dict[str, Any], group: list[Item], now: datetime) -> None:
    """Keep the search in the Cognitive Vault as an EXPERIENCE note: the need, the plan, the
    sources chosen and refused, the cost, the result and the markets. A failure to remember
    never disturbs the research itself."""
    try:
        e = entry["explanation"]
        refused = [
            f"{c['provider']}: {'; '.join(c['reasons'])}"
            for c in e["routing"]["candidates"]
            if not c["eligible"]
        ]
        lines = [
            f"Need: {e['plan']['need']} (level {e['plan']['level']})",
            f"Plan: {e['why'][0]['objective']}",
            f"Query: {e['plan']['query']}"
            if e["plan"]["query"]
            else f"Topic: {e['plan']['topic']}",
            f"Source chosen: {entry['provider']}",
            f"Sources refused: {' | '.join(refused) if refused else 'none'}",
            f"Cost: {e['cost']['units']} request(s), {e['cost']['money']} money",
            f"Result: {entry['status']} ({entry['detail'] or e['result']['kind']})",
            f"Links: {sum(len(v) for v in e.get('links', {}).values())}",
            f"Markets: {', '.join(entry['markets'][:20])}",
            f"Date: {now.isoformat()}",
        ]
        cognitive_vault.create_note(
            session,
            NoteDraft(
                title=f"Research {e['plan']['need']} {entry['request_id'][:8]}",
                type=NoteType.EXPERIENCE,
                content="\n".join(lines),
                provenance=Provenance(
                    created_by=Actor.AMBRE, source_refs=(f"intel_request:{entry['request_id']}",)
                ),
                evidence_level=EvidenceLevel.OBSERVED,
                context={"domain": group[0].plan.domain.value, "status": entry["status"]},
            ),
            now,
            allow_duplicate=True,
        )
    except Exception:
        logger.exception("could not record the search in the cognitive vault; research goes on")


def market_context(
    session: Session,
    plan: MarketPlan,
    registry: Registry,
    now: datetime,
    *,
    market_created_at: datetime,
) -> FeatureSet:
    """The dated context features of one market, from what the vault of observations holds now."""
    collected_at: datetime | None = None
    if plan.need is InfoNeed.RECENT_EARTHQUAKES:
        for card in registry.providers_for(plan.need):
            last = store.last_request(
                session, plan.need.value, card.id, CACHE_STATUSES, group_key="global"
            )
            if last is not None and (collected_at is None or last.created_at > collected_at):
                collected_at = last.created_at
    return build_feature_set(
        plan,
        store.market_observations(session, plan.market_id),
        registry,
        now,
        market_created_at=market_created_at,
        collected_at=collected_at,
    )


__all__ = ["MarketInfoState", "ResearchReport", "market_context", "research_pass"]

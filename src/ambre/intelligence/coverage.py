"""Information coverage report: how far Ambre's research reaches over the unresolved markets.

Every unresolved market is in exactly one bucket: its stored information state, or NOT_ANALYZED
when no pass has looked at it. The buckets always sum to the total, and every ratio is given with
its denominator, so no percentage can hide the population it is a percentage of.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ambre.config.settings import Settings
from ambre.infrastructure.orm import MarketRow
from ambre.intelligence.news_ingest import news_metrics
from ambre.intelligence.orm import (
    IntelFeatureJournalRow,
    IntelJobRow,
    IntelJobRunRow,
    IntelMarketLinkRow,
    IntelMarketStateRow,
    IntelObservationRow,
    IntelRequestRow,
)
from ambre.intelligence.registry import load_registry
from ambre.intelligence.states import MarketInfoState


def _ratio(name: str, numerator: int, denominator: int, of: str) -> dict[str, Any]:
    return {
        "name": name,
        "numerator": numerator,
        "denominator": denominator,
        "of": of,
        "ratio": None if denominator == 0 else round(numerator / denominator, 4),
    }


#: A request that answered (even with nothing relevant); a FAILED one did not.
_ANSWERED = ("OK", "EMPTY")


def _topics(rows: list[IntelMarketStateRow]) -> dict[str, Any]:
    """How much the researchable markets share one search. A market without a topic key
    (weather, earthquakes: grouped by place elsewhere) counts as a topic of its own."""
    researchable = [r for r in rows if r.plan_status == "COLLECTABLE"]
    sizes: dict[str, int] = {}
    entities: set[str] = set()
    for r in researchable:
        key = r.topic_key if r.topic_key is not None else f"single|{r.market_id}"
        sizes[key] = sizes.get(key, 0) + 1
        entities.add(key.split("|", 1)[1])
    distinct = len(sizes)
    return {
        "researchable_markets": len(researchable),
        "distinct_topics": distinct,
        "singleton_topics": sum(1 for n in sizes.values() if n == 1),
        "largest_topic_markets": max(sizes.values(), default=0),
        "markets_per_topic": None if distinct == 0 else round(len(researchable) / distinct, 3),
        # topics if the domain were ignored: an upper bound on what merging could save
        "distinct_topics_ignoring_domain": len(entities),
    }


def _capacity(settings: Settings, waiting_topics: int) -> dict[str, Any]:
    """Arithmetic from the settings, not a measurement: the most calls the schedule allows."""
    cfg = settings.intelligence
    per_hour = cfg.max_calls_per_pass * 3600.0 / cfg.research_interval_seconds
    return {
        "max_calls_per_pass": cfg.max_calls_per_pass,
        "pass_interval_seconds": cfg.research_interval_seconds,
        "calls_per_hour_ceiling": round(per_hour, 2),
        "topics_waiting": waiting_topics,
        "hours_to_drain_ceiling": None
        if waiting_topics == 0
        else round(waiting_topics / per_hour, 3),
        "note": "ceiling of the pass schedule only; a provider's own quota or refusals come on top",
    }


def _diagnostics(
    session: Session, rows: list[IntelMarketStateRow], settings: Settings | None
) -> dict[str, Any]:
    failed = sum(1 for r in rows if r.last_outcome == "FAILED")
    answered = sum(
        1 for r in rows if r.last_researched_at is not None and r.last_outcome != "FAILED"
    )
    causes: dict[str, int] = {}
    for r in rows:
        if r.state == MarketInfoState.RESEARCH_DEFERRED.value:
            causes[r.reason] = causes.get(r.reason, 0) + 1
    by_status = {
        str(status): int(n)
        for status, n in session.execute(
            select(IntelRequestRow.status, func.count()).group_by(IntelRequestRow.status)
        ).all()
    }
    served = [
        len(ids)
        for (ids,) in session.execute(
            select(IntelRequestRow.market_ids).where(IntelRequestRow.status.in_(_ANSWERED))
        ).all()
        if ids
    ]
    runs = session.execute(
        select(func.count(), func.max(IntelJobRunRow.started_at)).select_from(IntelJobRunRow)
    ).one()
    topics = _topics(rows)
    waiting = len({r.topic_key or r.market_id for r in rows if r.state == "RESEARCH_DEFERRED"})
    out: dict[str, Any] = {
        "research": {
            "answered": answered,
            "attempted_failed": failed,
            "note": "answered = a search returned (possibly nothing relevant); failed = refused",
        },
        "deferred_causes": causes,
        "topics": topics,
        "requests": {
            "by_status": by_status,
            "successful": len(served),
            "markets_per_successful_request": (
                None if not served else round(sum(served) / len(served), 3)
            ),
        },
        "scheduler": {
            "jobs": session.scalar(select(func.count()).select_from(IntelJobRow)) or 0,
            "runs": int(runs[0]),
            "last_run_at": runs[1],
            "ever_ran": int(runs[0]) > 0,
        },
        "evidence": {
            "observations_by_provider": {
                str(p): int(n)
                for p, n in session.execute(
                    select(IntelObservationRow.provider_id, func.count()).group_by(
                        IntelObservationRow.provider_id
                    )
                ).all()
            },
            "market_links": session.scalar(select(func.count()).select_from(IntelMarketLinkRow))
            or 0,
            "links_usable_for_prediction": session.scalar(
                select(func.count())
                .select_from(IntelMarketLinkRow)
                .where(IntelMarketLinkRow.usable_for_prediction.is_(True))
            )
            or 0,
            "feature_journal_rows": session.scalar(
                select(func.count()).select_from(IntelFeatureJournalRow)
            )
            or 0,
        },
    }
    if settings is not None:
        out["capacity"] = _capacity(settings, waiting)
        out["news"] = news_metrics(
            session,
            load_registry(settings.providers_path),
            datetime.now(UTC),
            settings.intelligence.news_window_days,
        )
    return out


def coverage_report(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    total = (
        session.scalar(
            select(func.count()).select_from(MarketRow).where(MarketRow.is_resolved.is_(False))
        )
        or 0
    )
    rows = list(
        session.scalars(
            select(IntelMarketStateRow)
            .join(MarketRow, MarketRow.id == IntelMarketStateRow.market_id)
            .where(MarketRow.is_resolved.is_(False))
        )
    )
    analyzed = len(rows)
    by_state: dict[str, int] = {s.value: 0 for s in MarketInfoState}
    by_domain_state: dict[str, dict[str, int]] = {}
    by_plan: dict[str, int] = {}
    by_level: dict[str, int] = {"1": 0, "2": 0}
    for r in rows:
        by_state[r.state] += 1
        by_domain_state.setdefault(r.domain, {})
        by_domain_state[r.domain][r.state] = by_domain_state[r.domain].get(r.state, 0) + 1
        by_plan[r.plan_status] = by_plan.get(r.plan_status, 0) + 1
        by_level[str(r.level)] = by_level.get(str(r.level), 0) + 1
    by_state[MarketInfoState.NOT_ANALYZED.value] = total - analyzed

    def n(*states: MarketInfoState) -> int:
        return sum(by_state[s.value] for s in states)

    researchable = by_plan.get("COLLECTABLE", 0)
    researched = sum(1 for r in rows if r.last_researched_at is not None)
    with_evidence = sum(1 for r in rows if r.evidence_count > 0)
    requests = session.execute(
        select(
            IntelRequestRow.provider_id,
            func.count(),
            func.coalesce(func.sum(IntelRequestRow.cost_units), 0),
        )
        .where(IntelRequestRow.cost_units > 0)
        .group_by(IntelRequestRow.provider_id)
    ).all()
    cost_by_provider = {str(p): {"requests": int(c), "units": int(u)} for p, c, u in requests}
    return {
        "total_unresolved_markets": total,
        "reconciled": sum(by_state.values()) == total,
        "by_state": by_state,
        "by_plan_status": by_plan,
        "by_level": by_level,
        "by_domain_and_state": by_domain_state,
        "counts": {
            "understood": analyzed,
            "research_possible": researchable,
            "researched": researched,
            "with_evidence": with_evidence,
            "with_validated_model": n(MarketInfoState.READY_FOR_PREDICTION),
            "blocked_no_source": n(MarketInfoState.NO_AVAILABLE_SOURCE),
            "blocked_ambiguity": by_plan.get("AMBIGUOUS", 0) + by_plan.get("OUT_OF_HORIZON", 0),
            "deferred": n(MarketInfoState.RESEARCH_DEFERRED),
            "failed": n(MarketInfoState.RESEARCH_FAILED),
            "closed_waiting_for_resolution": by_plan.get("NOT_APPLICABLE", 0),
        },
        "ratios": [
            _ratio("understood", analyzed, total, "unresolved markets"),
            _ratio("research possible", researchable, analyzed, "analyzed markets"),
            _ratio("researched", researched, researchable, "markets with research possible"),
            _ratio("with evidence", with_evidence, researchable, "markets with research possible"),
            _ratio(
                "with a validated model",
                n(MarketInfoState.READY_FOR_PREDICTION),
                total,
                "unresolved markets",
            ),
        ],
        "cost": {
            "by_provider": cost_by_provider,
            "total_requests": sum(v["requests"] for v in cost_by_provider.values()),
            "total_units": sum(v["units"] for v in cost_by_provider.values()),
            "money": 0,
        },
        "diagnostics": _diagnostics(session, rows, settings),
        "notes": [
            "a market with a row is analysed; NOT_ANALYZED counts unresolved markets without one",
            "evidence is a relevant, linked report, not a verified fact and not a prediction",
        ],
    }

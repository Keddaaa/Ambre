"""Information coverage report: how far Ambre's research reaches over the unresolved markets.

Every unresolved market is in exactly one bucket: its stored information state, or NOT_ANALYZED
when no pass has looked at it. The buckets always sum to the total, and every ratio is given with
its denominator, so no percentage can hide the population it is a percentage of.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ambre.infrastructure.orm import MarketRow
from ambre.intelligence.orm import IntelMarketStateRow, IntelRequestRow
from ambre.intelligence.states import MarketInfoState


def _ratio(name: str, numerator: int, denominator: int, of: str) -> dict[str, Any]:
    return {
        "name": name,
        "numerator": numerator,
        "denominator": denominator,
        "of": of,
        "ratio": None if denominator == 0 else round(numerator / denominator, 4),
    }


def coverage_report(session: Session) -> dict[str, Any]:
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
        "notes": [
            "a market with a row is analysed; NOT_ANALYZED counts unresolved markets without one",
            "evidence is a relevant, linked report, not a verified fact and not a prediction",
        ],
    }

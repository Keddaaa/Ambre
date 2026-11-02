"""Generic planner (pure): from a market analysis to a collection plan, for any domain.

Collection and estimation are kept apart. A plan can be COLLECTABLE (a provider can be asked
for data worth having) while the market is still not ``estimable`` (the question leaves
something open). Domains without a connector are understood and reported, never faked.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from ambre.intelligence.domain_params import WeatherParams, contract_for
from ambre.intelligence.models import InfoNeed
from ambre.intelligence.planning import Gap, Gazetteer, plan_gaps
from ambre.intelligence.queries import Topic, topic_for
from ambre.intelligence.resolution import MarketDomain, ResolutionAnalysis

#: Documented forecast_days maximum of the Open-Meteo forecast endpoint (docs/API_CATALOG.md).
FORECAST_HORIZON_DAYS = 16


class PlanStatus(StrEnum):
    COLLECTABLE = "COLLECTABLE"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_HORIZON = "OUT_OF_HORIZON"
    NO_CONNECTOR = "NO_CONNECTOR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class MarketPlan:
    market_id: str
    domain: MarketDomain
    status: PlanStatus
    analysis: ResolutionAnalysis
    need: InfoNeed | None = None
    gap: Gap | None = None  # earthquake collection target
    weather: WeatherParams | None = None
    topic: Topic | None = None
    level: int = 1  # 1 = specialised connector, 2 = generalist search
    blocking: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def estimable(self) -> bool:
        """True only when data can be collected AND the question leaves nothing open."""
        return self.status is PlanStatus.COLLECTABLE and self.level == 1 and not self.blocking


def _specialised_plan(
    analysis: ResolutionAnalysis, gazetteer: Gazetteer, now: datetime
) -> MarketPlan:
    contract = contract_for(analysis)

    def make(
        status: PlanStatus,
        *,
        need: InfoNeed | None = None,
        gap: Gap | None = None,
        weather: WeatherParams | None = None,
        blocking: tuple[str, ...] = (),
        reasons: tuple[str, ...] = (),
    ) -> MarketPlan:
        return MarketPlan(
            market_id=analysis.market_id,
            domain=analysis.domain,
            status=status,
            analysis=analysis,
            need=need,
            gap=gap,
            weather=weather,
            blocking=blocking,
            reasons=reasons,
        )

    if analysis.closes_at is not None and analysis.closes_at <= now:
        return make(PlanStatus.NOT_APPLICABLE, reasons=("market already closed",))

    if analysis.domain is MarketDomain.EARTHQUAKE:
        gap = plan_gaps(analysis.market_id, analysis.question, analysis.closes_at, now, gazetteer)
        if gap is None:
            return make(PlanStatus.NOT_APPLICABLE, blocking=contract.blocking)
        return make(
            PlanStatus.COLLECTABLE,
            need=InfoNeed.RECENT_EARTHQUAKES,
            gap=gap,
            blocking=contract.blocking,
        )

    if analysis.domain is MarketDomain.WEATHER:
        params = contract.weather
        if contract.unsupported:
            return make(
                PlanStatus.NO_CONNECTOR,
                blocking=contract.blocking,
                reasons=("no connector for this weather phenomenon: understood, not collected",),
            )
        if params is None:
            return make(PlanStatus.AMBIGUOUS, blocking=contract.blocking)
        days_ahead = (params.target_date - now.date()).days
        if days_ahead < 0:
            return make(
                PlanStatus.AMBIGUOUS,
                blocking=(
                    *contract.blocking,
                    "target date is in the past: a forecast is not an observation",
                ),
            )
        if days_ahead >= FORECAST_HORIZON_DAYS:
            return make(
                PlanStatus.OUT_OF_HORIZON,
                blocking=(
                    *contract.blocking,
                    f"target date is {days_ahead} days away; the forecast horizon is "
                    f"{FORECAST_HORIZON_DAYS} days",
                ),
                reasons=("retry once the target date is inside the forecast horizon",),
            )
        return make(
            PlanStatus.COLLECTABLE,
            need=InfoNeed.WEATHER_FORECAST,
            weather=params,
            blocking=contract.blocking,
        )

    return make(
        PlanStatus.NO_CONNECTOR,
        blocking=contract.blocking,
        reasons=(f"no connector for domain {analysis.domain.value}: understood, not collected",),
    )


def plan_market(analysis: ResolutionAnalysis, gazetteer: Gazetteer, now: datetime) -> MarketPlan:
    """The collection plan of a market. No specialised connector is not a dead end: a market with
    a searchable topic falls back to the generalist news search (level 2). That search yields
    evidence only: a level-2 plan is never estimable."""
    plan = _specialised_plan(analysis, gazetteer, now)
    if plan.status is PlanStatus.NO_CONNECTOR:
        topic = topic_for(analysis)
        if topic is not None:
            return replace(
                plan,
                status=PlanStatus.COLLECTABLE,
                need=InfoNeed.GENERAL_NEWS,
                topic=topic,
                level=2,
                reasons=(
                    *plan.reasons,
                    "generalist news search: evidence only, the specialised contract is not met",
                ),
            )
    return plan

"""Per-domain parameter contracts and validators (pure).

Each domain declares what a market must state before data can be collected for it and before an
estimate could ever be made. ``contract_for`` returns the reasons a market is not ready (the
blockers) and, for weather, the validated parameters of the forecast to fetch. A blocker is a
fact about the question, never a default that would let the market through.
"""

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ambre.intelligence.resolution import (
    Comparator,
    MarketDomain,
    NumericCondition,
    ResolutionAnalysis,
)

SOURCE_REQUIRED = frozenset(
    {MarketDomain.WEATHER, MarketDomain.EARTHQUAKE, MarketDomain.FINANCE, MarketDomain.ECONOMY}
)
_HIGH = re.compile(r"\b(?:high|highest|maximum|max|hottest|daily high)\b", re.I)
_LOW = re.compile(r"\b(?:low|lowest|minimum|min|coldest|daily low)\b", re.I)
_RAIN = re.compile(r"\b(?:rain|rainfall|precipitation|snow|snowfall)\b", re.I)
_TEMPERATURE = re.compile(r"\b(?:temperature|degrees|hot|heat|cold)\b|°\s?[CF]", re.I)
Variable = Literal["temperature_max", "temperature_min", "precipitation_sum"]


def to_metric(value: float, unit: str | None) -> tuple[float, str] | None:
    """Convert to °C or mm. ``None`` when the unit is unknown: nothing is guessed."""
    if unit == "°C":
        return value, "°C"
    if unit == "°F":
        return (value - 32.0) * 5.0 / 9.0, "°C"
    if unit == "mm":
        return value, "mm"
    if unit == "in":
        return value * 25.4, "mm"
    if unit == "cm":
        return value * 10.0, "mm"
    return None


class WeatherParams(BaseModel):
    """What to fetch for a weather market, and the threshold in metric units."""

    model_config = ConfigDict(frozen=True)

    variable: Variable
    location_name: str
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    timezone: str
    target_date: date
    threshold_value: float | None = None
    threshold_unit: str | None = None
    comparator: Comparator | None = None

    @field_validator("threshold_value")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("threshold_value must be finite")
        return v

    @model_validator(mode="after")
    def _threshold_is_complete(self) -> "WeatherParams":
        if self.threshold_value is not None and (
            self.comparator is None or not self.threshold_unit
        ):
            raise ValueError("a threshold needs a comparator and a unit")
        return self


@dataclass(frozen=True)
class ContractResult:
    domain: MarketDomain
    blocking: tuple[str, ...]
    weather: WeatherParams | None = None
    #: True when the market is about something no connector can serve (e.g. a hurricane)
    unsupported: bool = False


def _temperature_threshold(analysis: ResolutionAnalysis) -> NumericCondition | None:
    for c in analysis.conditions:
        if c.unit in ("°F", "°C", "degrees", "mm", "in", "cm"):
            return c
    return None


def _weather(analysis: ResolutionAnalysis) -> ContractResult:
    blocking: list[str] = []
    q = analysis.question
    if not (_RAIN.search(q) or _TEMPERATURE.search(q)):
        return ContractResult(
            MarketDomain.WEATHER,
            (
                "weather phenomenon not supported: "
                "only daily temperature and rain or snow forecasts",
            ),
            unsupported=True,
        )
    if _RAIN.search(q):
        variable: Variable | None = "precipitation_sum"
    elif _LOW.search(q) and _TEMPERATURE.search(q):
        variable = "temperature_min"
    elif _HIGH.search(q) and _TEMPERATURE.search(q):
        variable = "temperature_max"
    else:
        variable = None
        blocking.append("temperature variable not stated: daily high, low or mean?")
    location = analysis.location
    if location is None or location.center is None or location.timezone is None:
        blocking.append("location not recognised with coordinates and timezone")
    period = analysis.period
    if period is None or period.start != period.end:
        blocking.append("event date not stated as one calendar day")
    cond = _temperature_threshold(analysis)
    metric: tuple[float, str] | None = None
    if variable == "precipitation_sum" and cond is None:
        blocking.append("rain threshold undefined: what amount counts as rain?")
    elif cond is not None:
        metric = to_metric(cond.value, cond.unit)
        if metric is None:
            blocking.append("temperature unit not stated (°F or °C)")
        elif cond.comparator is None:
            blocking.append("comparison direction not stated")
    elif variable is not None:
        blocking.append("temperature threshold not stated")
    if not analysis.reference_sources:
        blocking.append("resolution source not named (station or provider unknown)")

    weather: WeatherParams | None = None
    if (
        variable is not None
        and location is not None
        and location.center is not None
        and location.timezone is not None
        and period is not None
        and period.start == period.end
        and period.start is not None
        and not (cond is not None and metric is None)
    ):
        weather = WeatherParams(
            variable=variable,
            location_name=location.name,
            latitude=location.center[0],
            longitude=location.center[1],
            timezone=location.timezone,
            target_date=period.end,
            threshold_value=metric[0] if metric and cond and cond.comparator else None,
            threshold_unit=metric[1] if metric and cond and cond.comparator else None,
            comparator=cond.comparator if metric and cond else None,
        )
    return ContractResult(MarketDomain.WEATHER, tuple(blocking), weather)


def _earthquake(analysis: ResolutionAnalysis) -> ContractResult:
    blocking: list[str] = []
    if analysis.location is None:
        blocking.append("region not recognised by the gazetteer")
    if analysis.period is None:
        blocking.append("event date or period not stated")
    if not any(c.unit == "magnitude" for c in analysis.conditions):
        blocking.append("magnitude threshold not stated in the question")
    elif not any(c.unit == "magnitude" and c.comparator for c in analysis.conditions):
        blocking.append("magnitude comparison direction not stated")
    return ContractResult(MarketDomain.EARTHQUAKE, tuple(blocking))


def _needs(
    analysis: ResolutionAnalysis,
    domain: MarketDomain,
    *,
    date_word: str,
    actor: str | None,
    threshold: str | None,
) -> ContractResult:
    blocking: list[str] = []
    if analysis.period is None:
        blocking.append(f"{date_word} not stated")
    if actor is not None and not analysis.entities:
        blocking.append(f"no {actor} recognised")
    if threshold is not None and not analysis.conditions:
        blocking.append(f"{threshold} not stated")
    return ContractResult(domain, tuple(blocking))


def contract_for(analysis: ResolutionAnalysis) -> ContractResult:
    domain = analysis.domain
    if domain is MarketDomain.WEATHER:
        result = _weather(analysis)
    elif domain is MarketDomain.EARTHQUAKE:
        result = _earthquake(analysis)
    elif domain is MarketDomain.SPORT:
        result = _needs(
            analysis, domain, date_word="event date", actor="team or competition", threshold=None
        )
    elif domain is MarketDomain.ECONOMY:
        result = _needs(
            analysis, domain, date_word="event date", actor=None, threshold="indicator threshold"
        )
    elif domain is MarketDomain.FINANCE:
        result = _needs(
            analysis,
            domain,
            date_word="event date",
            actor="instrument",
            threshold="price threshold",
        )
    elif domain is MarketDomain.TECHNOLOGY:
        result = _needs(
            analysis, domain, date_word="deadline", actor="company or product", threshold=None
        )
    elif domain is MarketDomain.GEOPOLITICS:
        result = _needs(analysis, domain, date_word="deadline", actor="actor", threshold=None)
    elif domain is MarketDomain.SCIENCE:
        result = _needs(analysis, domain, date_word="deadline", actor="topic", threshold=None)
    else:
        return ContractResult(domain, ("no specialised contract for this domain",))
    blocking = result.blocking
    if (
        domain in SOURCE_REQUIRED
        and domain is not MarketDomain.WEATHER
        and not analysis.reference_sources
    ):
        blocking = (*blocking, "resolution source not named")
    return ContractResult(domain, blocking, result.weather, result.unsupported)

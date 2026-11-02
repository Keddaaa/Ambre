"""Domain parameter contracts and the generic planner (pure)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from ambre.intelligence.domain_params import (
    WeatherParams,
    contract_for,
    to_metric,
)
from ambre.intelligence.generic_planning import PlanStatus, plan_market
from ambre.intelligence.models import InfoNeed
from ambre.intelligence.planning import Gazetteer, Region
from ambre.intelligence.resolution import Comparator, MarketDomain, analyze_market, load_rules

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CLOSE = NOW + timedelta(days=60)
RULES = load_rules()
GAZ = Gazetteer(
    [
        Region(
            "new_york_city",
            ("new york city", "nyc"),
            (40.4, 41.0, -74.3, -73.7),
            center=(40.7128, -74.0060),
            timezone="America/New_York",
        ),
        Region("new_zealand", ("new zealand",), (-48.0, -33.0, 165.0, 179.0)),
        Region("san_francisco", ("san francisco",), (37.0, 38.5, -123.5, -121.5)),
    ]
)


def analysis(question: str, close_at: datetime | None = CLOSE):
    return analyze_market("synthetic_m1", question, close_at, NOW, GAZ, RULES)


def plan(question: str, close_at: datetime | None = CLOSE):
    return plan_market(analysis(question, close_at), GAZ, NOW)


# --------------------------------------------------------------------------- weather


def test_a_complete_weather_market_becomes_a_collectable_forecast_need() -> None:
    p = plan("Will the high temperature in NYC exceed 90°F on November 10, 2026?")
    assert p.status is PlanStatus.COLLECTABLE and p.need is InfoNeed.WEATHER_FORECAST
    params = p.weather
    assert params is not None
    assert params.variable == "temperature_max" and params.target_date == date(2026, 11, 10)
    assert (params.latitude, params.longitude) == (40.7128, -74.0060)
    assert params.timezone == "America/New_York"
    assert params.comparator is Comparator.GT
    assert params.threshold_unit == "°C" and round(params.threshold_value or 0, 3) == 32.222
    assert p.estimable is False  # no resolution source stated: collection yes, estimation no
    assert any("resolution source" in b for b in p.blocking)


def test_naming_the_resolution_source_and_everything_else_makes_it_estimable() -> None:
    p = plan(
        "According to NOAA, will the high temperature in NYC exceed 90°F on November 10, 2026?"
    )
    assert p.status is PlanStatus.COLLECTABLE and p.estimable and p.blocking == ()


def test_weather_without_a_date_location_or_variable_is_ambiguous_and_not_collected() -> None:
    for question, expected in [
        ("Will the high temperature in NYC exceed 90°F?", "event date"),
        ("Will the high temperature exceed 90°F on November 10, 2026?", "location"),
        ("Will the temperature in NYC exceed 90°F on November 10, 2026?", "high, low or mean"),
        ("Will it exceed 90 degrees in NYC on November 10, 2026?", "unit"),
    ]:
        p = plan(question)
        assert p.status is PlanStatus.AMBIGUOUS and p.need is None, question
        assert any(expected in b for b in p.blocking), (question, p.blocking)


def test_a_forecast_cannot_reach_beyond_its_horizon() -> None:
    far = plan("Will the high temperature in NYC exceed 90°F on March 3, 2027?")
    assert far.status is PlanStatus.OUT_OF_HORIZON and far.need is None
    assert any("16 days" in b for b in far.blocking)
    past = plan("Will the high temperature in NYC exceed 90°F on October 20, 2026?")
    assert past.status is PlanStatus.AMBIGUOUS
    assert any("past" in b for b in past.blocking)


def test_low_temperature_and_rain_variables() -> None:
    low = plan("Will the low temperature in NYC fall below 20°F on November 10, 2026?").weather
    assert low is not None and low.variable == "temperature_min"
    assert low.comparator is Comparator.LT
    rain = plan("Will it rain in NYC on November 10, 2026?")
    assert rain.status is PlanStatus.COLLECTABLE and rain.weather is not None
    assert rain.weather.variable == "precipitation_sum" and rain.weather.threshold_value is None
    assert any("rain" in b and "threshold" in b for b in rain.blocking)  # "rain" is undefined


def test_unit_conversion_to_metric() -> None:
    fahrenheit = to_metric(212.0, "°F")
    assert (
        fahrenheit is not None and fahrenheit[0] == pytest.approx(100.0) and fahrenheit[1] == "°C"
    )
    assert to_metric(20.0, "°C") == (20.0, "°C")
    inch = to_metric(1.0, "in")
    assert inch is not None and inch[0] == pytest.approx(25.4) and inch[1] == "mm"
    assert to_metric(2.0, "cm") == (20.0, "mm")
    assert to_metric(5.0, "degrees") is None  # unit unknown: no conversion is invented


def test_weather_params_validate_their_own_ranges() -> None:
    base = {
        "variable": "temperature_max",
        "location_name": "x",
        "latitude": 40.0,
        "longitude": -74.0,
        "timezone": "America/New_York",
        "target_date": date(2026, 11, 10),
    }
    assert WeatherParams(**base).threshold_value is None
    for bad in ({"latitude": 91.0}, {"longitude": -181.0}, {"threshold_value": float("nan")}):
        with pytest.raises(ValidationError):
            WeatherParams(**{**base, **bad})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        WeatherParams(**{**base, "threshold_value": 30.0})  # type: ignore[arg-type]  # needs a comparator


# ------------------------------------------------------------------------ earthquakes


def test_earthquake_markets_keep_their_existing_collection_behaviour() -> None:
    p = plan("Will there be a 6.7 or greater magnitude earthquake in San Francisco in 2026?")
    assert p.status is PlanStatus.COLLECTABLE and p.need is InfoNeed.RECENT_EARTHQUAKES
    assert p.gap is not None and p.gap.min_magnitude == 6.7
    assert p.gap.region is not None and p.gap.region.name == "san_francisco"


def test_an_earthquake_market_without_a_threshold_is_collected_but_not_estimable() -> None:
    p = plan("Will there be an earthquake causing at least 1 death in New Zealand by 2027?")
    assert p.status is PlanStatus.COLLECTABLE and not p.estimable
    assert any("threshold" in b for b in p.blocking)


# -------------------------------------------------------------------- other domains


@pytest.mark.parametrize(
    ("question", "domain"),
    [
        ("Will Bitcoin trade above $150,000 by December 31, 2026?", MarketDomain.CRYPTO),
        ("Will the CPI inflation rate exceed 4% in 2027?", MarketDomain.ECONOMY),
        ("Will OpenAI release a new model before 2027?", MarketDomain.TECHNOLOGY),
        ("Will the Lakers win the NBA championship in 2027?", MarketDomain.SPORT),
        ("Will a ceasefire be signed in Ukraine by 2027?", MarketDomain.GEOPOLITICS),
        ("Will a vaccine for X be approved by 2030?", MarketDomain.HEALTH),
        ("Will my cat like the new food?", MarketDomain.GENERAL),
    ],
)
def test_domains_without_a_specialised_connector_get_a_generalist_search_or_say_why_not(
    question: str, domain: MarketDomain
) -> None:
    p = plan(question)
    assert p.domain is domain
    assert not p.estimable  # no specialised data: never estimable
    if domain is MarketDomain.GENERAL:  # nothing searchable in the question
        assert p.status is PlanStatus.NO_CONNECTOR and p.need is None
        assert any("no connector" in r for r in p.reasons)
    else:  # no connector is not a dead end: the generalist search takes over
        assert p.status is PlanStatus.COLLECTABLE and p.need is InfoNeed.GENERAL_NEWS
        assert p.level == 2 and p.topic is not None
        assert any("generalist news search" in r for r in p.reasons)


def test_every_domain_has_a_contract_that_names_what_it_still_needs() -> None:
    for question, expected in [
        ("Will Bitcoin trade above $150,000?", "date"),
        ("Will the Lakers win the NBA championship?", "date"),
        ("Will inflation exceed 4%?", "date"),
        ("Will a ceasefire be signed?", "deadline"),
        ("Will a vaccine be approved?", "deadline"),
        ("Will OpenAI release a model?", "deadline"),
    ]:
        c = contract_for(analysis(question))
        assert any(expected in b for b in c.blocking), (question, c.blocking)


def test_a_general_market_has_no_specialised_contract() -> None:
    c = contract_for(analysis("Will my cat like the new food?"))
    assert any("no specialised contract" in b for b in c.blocking)


def test_a_closed_market_is_not_applicable() -> None:
    p = plan(
        "Will the high temperature in NYC exceed 90°F on November 10, 2026?",
        NOW - timedelta(days=1),
    )
    assert p.status is PlanStatus.NOT_APPLICABLE and p.need is None


def test_a_phenomenon_without_a_connector_is_not_dressed_up_as_a_temperature_question() -> None:
    p = plan("Will a hurricane make landfall in a US state (Pacific) in 2026?")
    assert p.domain is MarketDomain.WEATHER
    # no specialised connector, so the generalist search is used: evidence only, not estimable
    assert p.status is PlanStatus.COLLECTABLE and p.need is InfoNeed.GENERAL_NEWS and p.level == 2
    assert any("not supported" in b for b in p.blocking) and not p.estimable
    assert not any("temperature variable" in b for b in p.blocking)

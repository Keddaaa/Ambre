"""Resolution Intelligence Engine: what a market question actually states (pure, no LLM)."""

from datetime import UTC, date, datetime, timedelta

from ambre.intelligence.planning import Gazetteer, Region
from ambre.intelligence.resolution import (
    Comparator,
    MarketDomain,
    PeriodKind,
    analyze_market,
    load_rules,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CLOSE = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
RULES = load_rules()
GAZ = Gazetteer(
    [
        Region(
            "new_york_city",
            ("new york city", "nyc", "new york"),
            (40.4, 41.0, -74.3, -73.7),
            center=(40.7128, -74.0060),
            timezone="America/New_York",
        ),
        Region("new_zealand", ("new zealand",), (-48.0, -33.0, 165.0, 179.0)),
    ]
)


def analyze(question: str, close_at: datetime | None = CLOSE):
    return analyze_market("synthetic_m1", question, close_at, NOW, GAZ, RULES)


def test_the_four_clocks_of_a_market_are_never_confused() -> None:
    a = analyze("Will the high temperature in New York City exceed 90°F on July 4, 2027?")
    assert a.closes_at == CLOSE  # when betting stops
    assert a.period is not None and a.period.kind is PeriodKind.ON
    assert a.period.start == a.period.end == date(2027, 7, 4)  # when the thing must happen
    assert a.period.end != a.closes_at.date()


def test_a_missing_event_date_is_reported_and_the_close_date_is_not_used_instead() -> None:
    a = analyze("Will the high temperature in New York City exceed 90°F?")
    assert a.period is None
    assert any("event date" in m for m in a.missing)
    assert any("close date is not the event date" in x for x in a.ambiguities)


def test_a_date_without_a_year_is_not_guessed() -> None:
    a = analyze("Will it exceed 90°F in New York City on July 4?")
    assert a.period is None
    assert any("year" in x for x in a.ambiguities)


def test_threshold_unit_and_direction_are_extracted_with_their_yes_and_no_conditions() -> None:
    a = analyze("Will the high temperature in New York City exceed 90°F on July 4, 2027?")
    (c,) = a.conditions
    assert (c.value, c.unit, c.comparator) == (90.0, "°F", Comparator.GT)
    assert a.yes_condition is not None and "> 90°F" in a.yes_condition
    assert a.no_condition is not None and "<= 90°F" in a.no_condition


def test_inclusive_and_exclusive_phrases_differ() -> None:
    at_least = analyze("Will inflation be at least 3.5% in 2027?").conditions[0]
    above = analyze("Will inflation be above 3.5% in 2027?").conditions[0]
    below = analyze("Will inflation fall below 2% in 2027?").conditions[0]
    assert at_least.comparator is Comparator.GE and above.comparator is Comparator.GT
    assert below.comparator is Comparator.LT and below.unit == "%"


def test_a_number_without_a_stated_direction_is_an_ambiguity_not_a_guess() -> None:
    a = analyze("Will New York City reach 90°F by August 1, 2027?")
    assert a.conditions[0].value == 90.0
    b = analyze("Is the 2026 temperature 90°F in New York City?")
    assert b.conditions[0].comparator is None
    assert any("direction" in x for x in b.ambiguities)
    assert b.yes_condition is None  # no invented criterion


def test_earthquake_magnitude_is_read_as_a_threshold_and_deaths_are_not_a_magnitude() -> None:
    a = analyze("Will there be a 6.7 or greater magnitude earthquake in New Zealand in 2026?")
    assert a.domain is MarketDomain.EARTHQUAKE
    assert (a.conditions[0].value, a.conditions[0].unit) == (6.7, "magnitude")
    assert a.conditions[0].comparator is Comparator.GE
    assert a.location is not None and a.location.name == "new_zealand"
    assert a.period is not None and a.period.kind is PeriodKind.IN_YEAR
    assert (a.period.start, a.period.end) == (date(2026, 1, 1), date(2026, 12, 31))
    deaths = analyze("Will an earthquake cause 100 or more deaths in New Zealand?")
    assert deaths.conditions == ()


def test_deadline_phrases() -> None:
    by = analyze("Will X happen by December 31, 2026?").period
    before = analyze("Will X happen before January 1, 2027?").period
    assert by is not None and by.end == date(2026, 12, 31) and by.start is None
    assert before is not None and before.end == date(2026, 12, 31)  # strictly before Jan 1
    eoy = analyze("Will a Nobel be awarded for X by the end of 2026?").period
    assert eoy is not None and eoy.end == date(2026, 12, 31)


def test_a_year_deadline_with_by_is_flagged_ambiguous() -> None:
    a = analyze("Will humans land on Mars by 2030?")
    assert a.period is not None and a.period.end == date(2030, 12, 31)
    assert any("deadline" in x for x in a.ambiguities)


def test_reference_sources_are_read_only_when_the_question_names_one() -> None:
    named = analyze("According to NOAA, will New York City exceed 90°F on July 4, 2027?")
    assert "NOAA" in named.reference_sources
    silent = analyze("Will New York City exceed 90°F on July 4, 2027?")
    assert silent.reference_sources == ()
    assert any("resolution source" in m for m in silent.missing)


def test_the_stored_description_is_never_assumed() -> None:
    a = analyze("Will New York City exceed 90°F on July 4, 2027?")
    assert any("description" in m for m in a.missing)


def test_domains_are_classified_from_configurable_keywords() -> None:
    cases = {
        "Will the CPI inflation rate exceed 4% in 2027?": MarketDomain.ECONOMY,
        "Will Bitcoin trade above $150,000 by December 31, 2026?": MarketDomain.CRYPTO,
        "Will OpenAI release a new model before 2027?": MarketDomain.TECHNOLOGY,
        "Will the Lakers win the NBA championship in 2027?": MarketDomain.SPORT,
        "Will a ceasefire be signed in Ukraine by 2027?": MarketDomain.GEOPOLITICS,
        "Will a vaccine for X be approved by 2030?": MarketDomain.HEALTH,
        "Will it rain in Paris tomorrow?": MarketDomain.WEATHER,
        "Will my cat like the new food?": MarketDomain.GENERAL,
    }
    for question, domain in cases.items():
        assert analyze(question).domain is domain, question


def test_a_question_matching_two_domains_equally_is_flagged() -> None:
    a = analyze("Will the president visit the NBA finals?")
    assert any("domain" in x for x in a.ambiguities)


def test_entities_and_dollar_thresholds() -> None:
    a = analyze("Will Bitcoin trade above $150,000 by December 31, 2026?")
    assert "Bitcoin" in a.entities
    c = a.conditions[0]
    assert (c.value, c.unit, c.comparator) == (150000.0, "USD", Comparator.GT)


def test_an_unknown_close_time_and_a_closed_market_are_reported() -> None:
    assert analyze("Will X happen by 2027?", None).closes_at is None
    closed = analyze("Will X happen by 2027?", NOW - timedelta(days=1))
    assert any("already closed" in x for x in closed.ambiguities)


def test_analysis_is_deterministic_and_does_not_invent_a_subject() -> None:
    q = "Will the high temperature in New York City exceed 90°F on July 4, 2027?"
    assert analyze(q) == analyze(q)
    assert analyze(q).subject == q.rstrip("?")


def test_a_date_can_name_its_weekday() -> None:
    """Seen on the Paris-Montsouris series: no period, so no forecast was ever requested."""
    a = analyze("Will Paris-Montsouris record at least 1 mm of rain on Thursday, November 5, 2026?")
    assert a.period is not None
    assert a.period.kind is PeriodKind.ON
    assert a.period.start == a.period.end == date(2026, 11, 5)
    assert analyze("Will X happen by Friday, December 31, 2027?").period is not None

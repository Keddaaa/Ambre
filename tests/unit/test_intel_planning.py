"""Information Planner: what a market lacks, read from its question only (pure)."""

from datetime import UTC, datetime, timedelta

from ambre.intelligence.models import InfoNeed
from ambre.intelligence.planning import Gazetteer, Region, parse_gazetteer, plan_gaps

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
GAZ = Gazetteer(
    [
        Region(name="new_zealand", aliases=("new zealand",), bbox=(-48.0, -33.0, 165.0, 179.0)),
        Region(
            name="san_francisco",
            aliases=("san francisco", "bay area"),
            bbox=(37.0, 38.5, -123.5, -121.5),
        ),
    ]
)


def plan(question: str, close_at: datetime | None = NOW + timedelta(days=30)):
    return plan_gaps("synthetic_m1", question, close_at, NOW, GAZ)


def test_earthquake_question_with_threshold_and_region() -> None:
    gap = plan("Will there be a 6.7 or greater magnitude earthquake in San Francisco in 2026?")
    assert gap is not None
    assert gap.need is InfoNeed.RECENT_EARTHQUAKES
    assert gap.min_magnitude == 6.7
    assert gap.region is not None and gap.region.name == "san_francisco"
    assert "earthquake" in gap.objective


def test_threshold_unknown_stays_none_not_a_default() -> None:
    gap = plan("Will there be an earthquake causing at least 1 death in New Zealand by 2027?")
    assert gap is not None
    assert gap.min_magnitude is None
    assert gap.region is not None and gap.region.name == "new_zealand"
    assert any("threshold" in u for u in gap.unresolved)


def test_death_count_is_not_mistaken_for_a_magnitude() -> None:
    gap = plan("Will an earthquake cause 100 or more deaths?")
    assert gap is not None and gap.min_magnitude is None


def test_implausible_magnitude_is_ignored() -> None:
    gap = plan("Will there be a 15 or greater magnitude earthquake?")
    assert gap is not None and gap.min_magnitude is None


def test_unknown_region_is_reported_not_guessed() -> None:
    gap = plan("Will a 7.0 or greater earthquake hit Atlantis?")
    assert gap is not None and gap.region is None and gap.min_magnitude == 7.0
    assert any("region" in u for u in gap.unresolved)


def test_resolution_criteria_are_never_assumed_read() -> None:
    gap = plan("Will there be a 6.7 or greater magnitude earthquake in San Francisco?")
    assert gap is not None
    assert any("resolution criteria" in u for u in gap.unresolved)


def test_non_earthquake_markets_have_no_gap() -> None:
    assert plan("Will it rain in Paris tomorrow?") is None
    assert plan("Will the Russia-Ukraine war end before 2027?") is None


def test_a_word_that_merely_contains_a_keyword_is_not_a_match() -> None:
    assert plan("Will the earthquakeless-index be above 3?") is None


def test_closed_or_unknown_close_market_gets_no_gap() -> None:
    assert (
        plan("Will a 7.0 or greater earthquake hit New Zealand?", NOW - timedelta(days=1)) is None
    )
    # unknown close time: still a gap, but the planner says the horizon is unknown
    gap = plan("Will a 7.0 or greater earthquake hit New Zealand?", None)
    assert gap is not None and any("close" in u for u in gap.unresolved)


def test_gazetteer_parsing_and_longest_alias_wins() -> None:
    gaz = parse_gazetteer(
        {
            "regions": {
                "california": {"aliases": ["california"], "bbox": [32.0, 42.0, -125.0, -114.0]},
                "san_francisco": {
                    "aliases": ["san francisco"],
                    "bbox": [37.0, 38.5, -123.5, -121.5],
                },
            }
        }
    )
    found = gaz.find("a quake near San Francisco, California")
    assert found is not None and found.name == "san_francisco"
    assert gaz.find("nowhere") is None


def test_region_contains_and_text_match() -> None:
    r = GAZ.find("san francisco")
    assert r is not None
    assert r.contains(37.8, -122.4)
    assert not r.contains(34.0, -118.2)
    assert not r.contains(None, -122.4)  # an unknown coordinate never matches
    assert r.matches_text("5 km W of Daly City, Bay Area")
    assert not r.matches_text(None)

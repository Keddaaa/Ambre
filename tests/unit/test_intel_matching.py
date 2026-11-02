"""Market matching: link an observation to a market gap with its evidence level (pure)."""

from datetime import UTC, datetime, timedelta

from ambre.intelligence.matching import CriteriaStatus, match_observation
from ambre.intelligence.models import (
    Domain,
    EarthquakePayload,
    InfoNeed,
    Observation,
    ObservationKind,
    VerificationStatus,
    make_observation_id,
)
from ambre.intelligence.planning import Gap, Region

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CREATED = NOW - timedelta(days=30)
NZ = Region(name="new_zealand", aliases=("new zealand",), bbox=(-48.0, -33.0, 165.0, 179.0))


def gap(min_magnitude: float | None = 6.5, region: Region | None = NZ) -> Gap:
    return Gap(
        market_id="synthetic_m1",
        need=InfoNeed.RECENT_EARTHQUAKES,
        objective="observe earthquakes",
        min_magnitude=min_magnitude,
        region=region,
        reasons=("question mentions an earthquake",),
        unresolved=("resolution criteria not read (the market description is not stored)",),
    )


def quake_obs(
    magnitude: float | None = 6.8,
    place: str | None = "10 km N of Synthetictown, New Zealand",
    lat: float | None = -41.0,
    lon: float | None = 174.0,
    event_at: datetime | None = NOW - timedelta(days=2),
    status: VerificationStatus = VerificationStatus.CONFIRMED,
    native_id: str = "synthetic_eq1",
) -> Observation:
    return Observation(
        observation_id=make_observation_id("usgs_earthquakes", native_id, "r1"),
        provider_id="usgs_earthquakes",
        domain=Domain.SEISMIC,
        kind=ObservationKind.OBSERVATION,
        payload=EarthquakePayload(
            native_id=native_id, magnitude=magnitude, place=place, latitude=lat, longitude=lon
        ),
        ingested_at=NOW,
        event_at=event_at,
        available_at=NOW - timedelta(days=2),
        verification_status=status,
    )


def test_a_matching_event_is_a_candidate_never_a_verified_resolution() -> None:
    link = match_observation(quake_obs(), gap(), market_created_at=CREATED, now=NOW)
    assert link is not None
    assert link.relation == "candidate_event"
    assert link.criteria_status is CriteriaStatus.THRESHOLD_MET_CRITERIA_UNREAD
    assert link.evidence_level == "confirmed"
    # the resolution criteria were not read, so no link may inform a prediction yet
    assert link.usable_for_prediction is False
    assert any("magnitude 6.8 >= 6.5" in r for r in link.reasons)


def test_below_threshold_has_no_link() -> None:
    assert (
        match_observation(quake_obs(magnitude=6.4), gap(), market_created_at=CREATED, now=NOW)
        is None
    )


def test_unknown_magnitude_is_not_treated_as_zero_nor_as_a_match() -> None:
    assert (
        match_observation(quake_obs(magnitude=None), gap(), market_created_at=CREATED, now=NOW)
        is None
    )


def test_event_before_the_market_existed_cannot_resolve_it() -> None:
    old = quake_obs(event_at=CREATED - timedelta(days=1))
    assert match_observation(old, gap(), market_created_at=CREATED, now=NOW) is None


def test_unknown_event_time_cannot_be_placed_in_the_market_window() -> None:
    assert (
        match_observation(quake_obs(event_at=None), gap(), market_created_at=CREATED, now=NOW)
        is None
    )


def test_event_not_yet_available_at_decision_time_is_not_used() -> None:
    late = quake_obs().model_copy(update={"available_at": NOW + timedelta(hours=1)})
    assert match_observation(late, gap(), market_created_at=CREATED, now=NOW) is None


def test_region_is_matched_by_place_text_or_bounding_box() -> None:
    by_text = quake_obs(lat=None, lon=None)
    assert match_observation(by_text, gap(), market_created_at=CREATED, now=NOW) is not None
    by_box = quake_obs(place="remote ocean", lat=-41.0, lon=174.0)
    assert match_observation(by_box, gap(), market_created_at=CREATED, now=NOW) is not None
    elsewhere = quake_obs(place="somewhere in Chile", lat=-33.5, lon=-70.6)
    assert match_observation(elsewhere, gap(), market_created_at=CREATED, now=NOW) is None


def test_unknown_region_in_the_question_gives_no_link() -> None:
    assert (
        match_observation(quake_obs(), gap(region=None), market_created_at=CREATED, now=NOW) is None
    )


def test_unknown_threshold_keeps_the_link_but_says_the_threshold_is_unknown() -> None:
    link = match_observation(
        quake_obs(), gap(min_magnitude=None), market_created_at=CREATED, now=NOW
    )
    assert link is not None
    assert link.criteria_status is CriteriaStatus.THRESHOLD_UNKNOWN


def test_unverified_observation_is_linked_with_its_weak_evidence_level() -> None:
    link = match_observation(
        quake_obs(status=VerificationStatus.UNVERIFIED), gap(), market_created_at=CREATED, now=NOW
    )
    assert link is not None and link.evidence_level == "unverified"
    assert link.usable_for_prediction is False


def test_retracted_observation_is_never_linked() -> None:
    gone = quake_obs(status=VerificationStatus.RETRACTED)
    assert match_observation(gone, gap(), market_created_at=CREATED, now=NOW) is None

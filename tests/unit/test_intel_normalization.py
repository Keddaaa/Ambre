"""USGS GeoJSON -> typed observations (pure). Fixtures are hand-written, ids prefixed synthetic_."""

from datetime import UTC, datetime, timedelta
from typing import Any

from ambre.intelligence.models import (
    Domain,
    ObservationKind,
    ResultKind,
    VerificationStatus,
    is_actionable,
)
from ambre.intelligence.normalization import parse_usgs_geojson

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
T_EVENT = NOW - timedelta(hours=3)
T_UPDATED = NOW - timedelta(hours=1)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def feature(fid: str = "synthetic_eq1", **props: Any) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "mag": 6.8,
        "place": "10 km N of Synthetictown, New Zealand",
        "time": ms(T_EVENT),
        "updated": ms(T_UPDATED),
        "status": "reviewed",
        "tsunami": 0,
        "url": "https://example.invalid/eq/synthetic_eq1",
        "title": "M 6.8 - 10 km N of Synthetictown, New Zealand",
    }
    properties.update(props)
    return {
        "type": "Feature",
        "id": fid,
        "properties": properties,
        "geometry": {"type": "Point", "coordinates": [174.5, -41.2, 12.5]},
    }


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


def test_reviewed_event_becomes_a_confirmed_observation_with_four_clocks() -> None:
    r = parse_usgs_geojson(collection(feature()), NOW)
    assert r.kind is ResultKind.OK and len(r.observations) == 1
    o = r.observations[0]
    assert (o.provider_id, o.domain, o.kind) == (
        "usgs_earthquakes",
        Domain.SEISMIC,
        ObservationKind.OBSERVATION,
    )
    assert o.verification_status is VerificationStatus.CONFIRMED and is_actionable(o)
    assert o.event_at == T_EVENT
    assert o.available_at == T_UPDATED  # when this version existed at the source
    assert o.published_at is None  # USGS gives no separate publication time
    assert o.ingested_at == NOW
    assert o.payload.magnitude == 6.8 and o.payload.depth_km == 12.5
    assert (o.payload.latitude, o.payload.longitude) == (-41.2, 174.5)
    assert o.payload.review_status == "reviewed" and o.payload.tsunami is False
    assert o.source_url == "https://example.invalid/eq/synthetic_eq1"


def test_automatic_solution_is_unverified_and_not_actionable() -> None:
    o = parse_usgs_geojson(collection(feature(status="automatic")), NOW).observations[0]
    assert o.verification_status is VerificationStatus.UNVERIFIED and not is_actionable(o)


def test_deleted_event_is_retracted() -> None:
    o = parse_usgs_geojson(collection(feature(status="deleted")), NOW).observations[0]
    assert o.verification_status is VerificationStatus.RETRACTED and not is_actionable(o)


def test_a_revision_is_a_new_observation_and_never_rewrites_the_old_one() -> None:
    a = parse_usgs_geojson(collection(feature(updated=ms(T_UPDATED))), NOW).observations[0]
    b = parse_usgs_geojson(
        collection(feature(updated=ms(T_UPDATED + timedelta(minutes=30)), mag=6.9)), NOW
    ).observations[0]
    assert a.observation_id != b.observation_id
    assert a.payload.native_id == b.payload.native_id
    assert (a.payload.magnitude, b.payload.magnitude) == (6.8, 6.9)


def test_same_record_same_revision_has_the_same_id() -> None:
    a = parse_usgs_geojson(collection(feature()), NOW).observations[0]
    b = parse_usgs_geojson(collection(feature()), NOW + timedelta(minutes=5)).observations[0]
    assert a.observation_id == b.observation_id


def test_missing_values_stay_none_never_zero() -> None:
    f = feature(mag=None, tsunami=None, updated=None, status=None, url=None)
    f["geometry"] = None
    o = parse_usgs_geojson(collection(f), NOW).observations[0]
    assert o.payload.magnitude is None and o.payload.tsunami is None
    assert o.payload.latitude is None and o.payload.longitude is None
    assert o.payload.depth_km is None
    assert o.available_at is None and o.source_url is None
    assert o.verification_status is VerificationStatus.UNVERIFIED


def test_invalid_items_are_counted_and_skipped_not_fixed() -> None:
    bad_time = feature("synthetic_bad_time", time=253402329540000)  # year 9999
    no_time = feature("synthetic_no_time", time=None)
    no_id = feature("")
    bad_lat = feature("synthetic_bad_lat")
    bad_lat["geometry"]["coordinates"] = [10.0, 123.0, 1.0]
    nan_mag = feature("synthetic_nan", mag=float("nan"))
    good = feature("synthetic_good")
    r = parse_usgs_geojson(collection(bad_time, no_time, no_id, bad_lat, nan_mag, good), NOW)
    assert r.kind is ResultKind.OK
    assert [o.payload.native_id for o in r.observations] == ["synthetic_good"]
    assert r.invalid_items == 5


def test_empty_collection_is_empty_not_failed() -> None:
    r = parse_usgs_geojson(collection(), NOW)
    assert r.kind is ResultKind.EMPTY and r.observations == ()


def test_unexpected_bodies_are_inconclusive() -> None:
    for body in ({}, [], "x", None, {"features": "nope"}, {"type": "FeatureCollection"}):
        r = parse_usgs_geojson(body, NOW)
        assert r.kind is ResultKind.INCONCLUSIVE, body
        assert r.observations == ()


def test_all_items_invalid_is_inconclusive() -> None:
    r = parse_usgs_geojson(collection(feature("", time=None)), NOW)
    assert r.kind is ResultKind.INCONCLUSIVE and r.invalid_items == 1

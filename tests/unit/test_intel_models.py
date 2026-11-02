"""Typed information envelope, provider cards and result kinds (pure, no network)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ambre.intelligence.models import (
    Domain,
    EarthquakePayload,
    FetchResult,
    InfoNeed,
    Observation,
    ObservationKind,
    ProviderCard,
    ProviderStatus,
    RateLimits,
    ResultKind,
    VerificationStatus,
    is_actionable,
    make_observation_id,
)

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def quake(**kw: object) -> EarthquakePayload:
    base: dict[str, object] = {"native_id": "synthetic_eq1", "magnitude": 6.8}
    base.update(kw)
    return EarthquakePayload(**base)  # type: ignore[arg-type]


def obs(**kw: object) -> Observation:
    base: dict[str, object] = {
        "observation_id": make_observation_id("usgs_earthquakes", "synthetic_eq1", "r1"),
        "provider_id": "usgs_earthquakes",
        "domain": Domain.SEISMIC,
        "kind": ObservationKind.OBSERVATION,
        "payload": quake(),
        "ingested_at": T,
        "event_at": T - timedelta(hours=2),
        "available_at": T - timedelta(hours=1),
        "verification_status": VerificationStatus.CONFIRMED,
    }
    base.update(kw)
    return Observation(**base)  # type: ignore[arg-type]


def test_unknown_fields_stay_none_never_zero() -> None:
    o = obs(payload=quake(magnitude=None, place=None, depth_km=None), published_at=None)
    assert o.payload.magnitude is None and o.payload.depth_km is None
    assert o.published_at is None and o.source_reliability is None


def test_observation_id_is_deterministic_and_revision_sensitive() -> None:
    a = make_observation_id("p", "n1", "r1")
    assert a == make_observation_id("p", "n1", "r1")
    assert a != make_observation_id("p", "n1", "r2")
    assert a != make_observation_id("q", "n1", "r1")


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValidationError):
        obs(event_at=datetime(2026, 11, 2, 12, 0))


def test_envelope_is_frozen() -> None:
    o = obs()
    with pytest.raises(ValidationError):
        o.title = "changed"  # type: ignore[misc]


def test_payload_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValidationError):
        quake(magnitude=float("nan"))
    with pytest.raises(ValidationError):
        quake(depth_km=float("inf"))


def test_payload_range_checks() -> None:
    with pytest.raises(ValidationError):
        quake(latitude=91.0)
    with pytest.raises(ValidationError):
        quake(longitude=-181.0)


def test_only_confirmed_or_corroborated_is_actionable() -> None:
    assert is_actionable(obs(verification_status=VerificationStatus.CONFIRMED))
    assert is_actionable(obs(verification_status=VerificationStatus.CORROBORATED))
    for status in (
        VerificationStatus.UNVERIFIED,
        VerificationStatus.CONTRADICTED,
        VerificationStatus.OUTDATED,
        VerificationStatus.RETRACTED,
    ):
        assert not is_actionable(obs(verification_status=status))


def test_a_forecast_is_never_actionable_as_an_observation() -> None:
    assert not is_actionable(obs(kind=ObservationKind.FORECAST))


def test_rate_limits_reject_non_positive_values() -> None:
    with pytest.raises(ValidationError):
        RateLimits(per_minute=0)
    with pytest.raises(ValidationError):
        RateLimits(min_interval_seconds=-1.0)
    assert RateLimits().is_empty
    assert not RateLimits(per_day=10).is_empty


def card(**kw: object) -> ProviderCard:
    base: dict[str, object] = {
        "id": "usgs_earthquakes",
        "name": "USGS",
        "domain": Domain.SEISMIC,
        "status": ProviderStatus.VERIFIED_FREE,
        "capabilities": {InfoNeed.RECENT_EARTHQUAKES: 0.9},
    }
    base.update(kw)
    return ProviderCard(**base)  # type: ignore[arg-type]


def test_provider_card_defaults_are_conservative() -> None:
    c = card()
    assert c.enabled is False
    assert c.allow_commercial_use is None  # unknown, not False and not True
    assert c.internal_budget.is_empty
    assert c.provides(InfoNeed.RECENT_EARTHQUAKES)


def test_relevance_prior_must_be_a_probability_like_weight() -> None:
    with pytest.raises(ValidationError):
        card(capabilities={InfoNeed.RECENT_EARTHQUAKES: 1.5})


def test_fetch_result_kinds_are_distinct() -> None:
    assert FetchResult(kind=ResultKind.EMPTY).observations == ()
    inconclusive = FetchResult(kind=ResultKind.INCONCLUSIVE, detail="empty object")
    assert inconclusive.kind is not ResultKind.EMPTY
    assert FetchResult(kind=ResultKind.OK, observations=(obs(),)).observations[0].provider_id

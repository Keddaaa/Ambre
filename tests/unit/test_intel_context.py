"""Context Feature Bridge and estimation gate (pure): only validated, already-available
information becomes a feature, and no probability is invented when no valid model exists."""

from datetime import UTC, datetime, timedelta

import pytest

from ambre.feature_engine.features import FeatureVector
from ambre.intelligence.context import augment_vector, build_feature_set
from ambre.intelligence.estimation import EstimationStatus, estimate_from_context
from ambre.intelligence.generic_planning import plan_market
from ambre.intelligence.models import (
    Domain,
    EarthquakePayload,
    InfoNeed,
    Observation,
    ObservationKind,
    VerificationStatus,
    WeatherForecastPayload,
    make_observation_id,
)
from ambre.intelligence.planning import Gazetteer, Region
from ambre.intelligence.registry import parse_registry
from ambre.intelligence.resolution import analyze_market, load_rules

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CREATED = NOW - timedelta(days=30)
GAZ = Gazetteer(
    [
        Region(
            "new_york_city",
            ("nyc",),
            (40.4, 41.0, -74.3, -73.7),
            center=(40.7128, -74.006),
            timezone="America/New_York",
        ),
        Region("new_zealand", ("new zealand",), (-48.0, -33.0, 165.0, 179.0)),
    ]
)
REGISTRY = parse_registry(
    {
        "providers": {
            "open_meteo": {
                "name": "Open-Meteo",
                "domain": "weather",
                "status": "VERIFIED_LIMITED",
                "enabled": True,
                "internal_budget": {"per_day": 100},
                "capabilities": {"weather_forecast": 0.8},
            },
            "usgs_earthquakes": {
                "name": "USGS",
                "domain": "seismic",
                "status": "VERIFIED_FREE",
                "enabled": True,
                "internal_budget": {"per_day": 100},
                "capabilities": {"recent_earthquakes": 0.9},
            },
            "gdelt": {
                "name": "GDELT",
                "domain": "news",
                "status": "VERIFIED_LIMITED",
                "enabled": False,
            },
        }
    }
)
WEATHER_Q = "According to NOAA, will the high temperature in NYC exceed 90°F on November 10, 2026?"


def weather_plan(question: str = WEATHER_Q):
    a = analyze_market("m1", question, NOW + timedelta(days=30), NOW, GAZ, load_rules())
    return plan_market(a, GAZ, NOW)


def forecast(
    value: float | None,
    *,
    hours_ago: float = 1.0,
    target: str = "2026-11-10",
    variable: str = "temperature_max",
    lat: float = 40.71,
    lon: float = -73.99,
    available: bool = True,
    provider: str = "open_meteo",
    status: VerificationStatus = VerificationStatus.UNVERIFIED,
) -> Observation:
    at = NOW - timedelta(hours=hours_ago)
    return Observation(
        observation_id=make_observation_id(provider, f"{variable}|{target}", at.isoformat()),
        provider_id=provider,
        domain=Domain.WEATHER,
        kind=ObservationKind.FORECAST,
        payload=WeatherForecastPayload(
            variable=variable,
            target_date=target,
            value=value,
            unit="°C",
            latitude=lat,
            longitude=lon,
            timezone="America/New_York",
        ),  # type: ignore[arg-type]
        ingested_at=at,
        available_at=at if available else None,
        verification_status=status,
    )


def names(fs) -> dict[str, float | None]:
    return {f.name: f.value for f in fs.features}


def test_the_latest_available_forecast_becomes_dated_features_with_a_threshold_margin() -> None:
    plan = weather_plan()
    obs = [
        forecast(30.0, hours_ago=5),
        forecast(34.0, hours_ago=1),
        forecast(99.0, hours_ago=-3),
    ]  # last is in the future
    fs = build_feature_set(plan, obs, REGISTRY, NOW, market_created_at=CREATED)
    vals = names(fs)
    assert vals["weather.forecast.temperature_max"] == 34.0  # the newest one already known
    assert vals["weather.forecast.temperature_max.margin"] == pytest.approx(
        34.0 - 32.2222, abs=1e-3
    )
    assert vals["weather.forecast.age_seconds"] == 3600.0
    assert vals["weather.forecast.horizon_days"] == 8.0
    assert all(f.available_at <= NOW for f in fs.features)
    assert any(
        "after the decision time" in r for _, r in fs.rejected
    )  # the future reading was refused


def test_a_forecast_without_an_availability_time_is_refused_not_dated_by_guess() -> None:
    fs = build_feature_set(
        weather_plan(), [forecast(30.0, available=False)], REGISTRY, NOW, market_created_at=CREATED
    )
    assert fs.features == () and any("availability unknown" in r for _, r in fs.rejected)


def test_a_missing_value_is_not_turned_into_a_feature() -> None:
    fs = build_feature_set(
        weather_plan(), [forecast(None)], REGISTRY, NOW, market_created_at=CREATED
    )
    assert fs.features == () and any("no value" in r for _, r in fs.rejected)


def test_other_days_variables_places_and_providers_do_not_leak_in() -> None:
    other = [
        forecast(30.0, target="2026-11-11"),
        forecast(30.0, variable="temperature_min"),
        forecast(30.0, lat=51.5, lon=-0.1),
        forecast(30.0, provider="gdelt"),  # disabled provider: its data must not feed anything
    ]
    fs = build_feature_set(weather_plan(), other, REGISTRY, NOW, market_created_at=CREATED)
    assert fs.features == ()
    assert any("not enabled" in r for _, r in fs.rejected)


def test_an_unknown_provider_is_refused() -> None:
    fs = build_feature_set(
        weather_plan(),
        [forecast(30.0, provider="mystery")],
        REGISTRY,
        NOW,
        market_created_at=CREATED,
    )
    assert fs.features == () and any("not in the registry" in r for _, r in fs.rejected)


def quake(mag: float, status: VerificationStatus, hours_ago: float = 48.0) -> Observation:
    return Observation(
        observation_id=make_observation_id("usgs_earthquakes", f"q{mag}{status}", "r"),
        provider_id="usgs_earthquakes",
        domain=Domain.SEISMIC,
        kind=ObservationKind.OBSERVATION,
        payload=EarthquakePayload(
            native_id=f"q{mag}",
            magnitude=mag,
            place="x, New Zealand",
            latitude=-41.0,
            longitude=174.0,
        ),
        ingested_at=NOW,
        event_at=NOW - timedelta(hours=hours_ago),
        available_at=NOW - timedelta(hours=hours_ago - 1),
        verification_status=status,
    )


def test_only_confirmed_earthquakes_become_features_and_unreviewed_ones_are_refused() -> None:
    a = analyze_market(
        "m2",
        "Will there be a 6.5 or greater magnitude earthquake in New Zealand in 2026?",
        NOW + timedelta(days=60),
        NOW,
        GAZ,
        load_rules(),
    )
    plan = plan_market(a, GAZ, NOW)
    assert plan.need is InfoNeed.RECENT_EARTHQUAKES
    obs = [
        quake(6.8, VerificationStatus.CONFIRMED),
        quake(7.2, VerificationStatus.UNVERIFIED),
        quake(6.9, VerificationStatus.RETRACTED),
    ]
    fs = build_feature_set(plan, obs, REGISTRY, NOW, market_created_at=CREATED, collected_at=NOW)
    vals = names(fs)
    assert vals["quake.matching_event_count"] == 1.0 and vals["quake.max_matching_magnitude"] == 6.8
    assert len(fs.rejected) >= 1


def test_no_matching_earthquake_gives_a_real_zero_count_but_no_invented_magnitude() -> None:
    a = analyze_market(
        "m2",
        "Will there be a 6.5 or greater magnitude earthquake in New Zealand in 2026?",
        NOW + timedelta(days=60),
        NOW,
        GAZ,
        load_rules(),
    )
    plan = plan_market(a, GAZ, NOW)
    fs = build_feature_set(plan, [], REGISTRY, NOW, market_created_at=CREATED, collected_at=NOW)
    vals = names(fs)
    assert vals["quake.matching_event_count"] == 0.0  # a search ran and found none
    assert vals["quake.max_matching_magnitude"] is None  # unknown stays None


def test_without_any_completed_search_there_is_no_zero_count_to_claim() -> None:
    a = analyze_market(
        "m2",
        "Will there be a 6.5 or greater magnitude earthquake in New Zealand in 2026?",
        NOW + timedelta(days=60),
        NOW,
        GAZ,
        load_rules(),
    )
    fs = build_feature_set(plan_market(a, GAZ, NOW), [], REGISTRY, NOW, market_created_at=CREATED)
    assert fs.features == ()  # nothing was collected: "no events" would be an invented zero


def test_domains_without_a_connector_get_no_context_features() -> None:
    a = analyze_market(
        "m3",
        "Will OpenAI release a new model before 2027?",
        NOW + timedelta(days=60),
        NOW,
        GAZ,
        load_rules(),
    )
    assert (
        build_feature_set(
            plan_market(a, GAZ, NOW), [], REGISTRY, NOW, market_created_at=CREATED
        ).features
        == ()
    )


def test_augmenting_a_vector_prefixes_names_and_moves_availability_forward_only() -> None:
    base = FeatureVector("m1", NOW - timedelta(minutes=10), {"market_probability": 0.3})
    fs = build_feature_set(
        weather_plan(), [forecast(34.0, hours_ago=1)], REGISTRY, NOW, market_created_at=CREATED
    )
    out = augment_vector(base, fs, NOW)
    assert (
        out.values["market_probability"] == 0.3
        and out.values["ctx.weather.forecast.temperature_max"] == 34.0
    )
    assert out.available_at == NOW - timedelta(hours=1) or out.available_at == base.available_at
    assert out.available_at >= base.available_at
    assert base.values == {"market_probability": 0.3}  # the original vector is untouched


def test_a_context_feature_from_the_future_is_refused_by_the_bridge() -> None:
    base = FeatureVector("m1", NOW, {"market_probability": 0.3})
    fs = build_feature_set(
        weather_plan(), [forecast(34.0, hours_ago=1)], REGISTRY, NOW, market_created_at=CREATED
    )
    with pytest.raises(ValueError, match="after"):
        augment_vector(base, fs, NOW - timedelta(days=1))


# ------------------------------------------------------------------------- estimation


class FakeModel:
    def __init__(self, validated: bool, probability: float = 0.7, applies: bool = True) -> None:
        self.version, self.validated, self._p, self._applies = (
            "synthetic-model-v1",
            validated,
            probability,
            applies,
        )

    def applies_to(self, plan) -> bool:  # noqa: ANN001
        return self._applies

    def predict(self, features: dict[str, float | None]) -> float:
        return self._p


def ready_features():
    return build_feature_set(
        weather_plan(), [forecast(34.0)], REGISTRY, NOW, market_created_at=CREATED
    )


def test_with_no_model_the_answer_is_insufficient_model_and_never_a_probability() -> None:
    r = estimate_from_context(weather_plan(), ready_features(), [])
    assert r.status is EstimationStatus.INSUFFICIENT_MODEL and r.probability is None
    assert any("no validated model" in x for x in r.reasons)
    assert (
        "weather.forecast.temperature_max" in r.feature_names
    )  # the features exist; the model does not


def test_an_unvalidated_or_inapplicable_model_is_ignored() -> None:
    for model in (FakeModel(validated=False), FakeModel(validated=True, applies=False)):
        r = estimate_from_context(weather_plan(), ready_features(), [model])
        assert r.status is EstimationStatus.INSUFFICIENT_MODEL and r.probability is None


def test_an_ambiguous_market_is_not_estimable_even_with_a_validated_model() -> None:
    plan = weather_plan(
        "Will the high temperature in NYC exceed 90°F on November 10, 2026?"
    )  # no source
    r = estimate_from_context(plan, ready_features(), [FakeModel(validated=True)])
    assert r.status is EstimationStatus.NOT_ESTIMABLE and r.probability is None
    assert any("resolution source" in b for b in r.blocking)


def test_a_validated_applicable_model_on_a_clear_market_gives_a_probability_with_its_version() -> (
    None
):
    r = estimate_from_context(
        weather_plan(), ready_features(), [FakeModel(validated=True, probability=0.7)]
    )
    assert r.status is EstimationStatus.ESTIMATED and r.probability == 0.7
    assert r.model_version == "synthetic-model-v1"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1, 0.0, 1.0])
def test_an_invalid_model_output_is_never_passed_on(bad: float) -> None:
    r = estimate_from_context(
        weather_plan(), ready_features(), [FakeModel(validated=True, probability=bad)]
    )
    assert r.status is EstimationStatus.INSUFFICIENT_MODEL and r.probability is None
    assert any("invalid probability" in x for x in r.reasons)


def test_no_features_means_no_estimate_even_with_a_validated_model() -> None:
    empty = build_feature_set(weather_plan(), [], REGISTRY, NOW, market_created_at=CREATED)
    r = estimate_from_context(weather_plan(), empty, [FakeModel(validated=True)])
    assert r.status is EstimationStatus.INSUFFICIENT_MODEL and r.probability is None
    assert any("no context features" in x for x in r.reasons)

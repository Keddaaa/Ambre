from datetime import timedelta

import pytest
from builders import NOW, make_market, make_snapshot

from ambre.feature_engine.features import FEATURE_SPECS, extract_features

EXPECTED_KEYS = {
    "market_probability",
    "total_liquidity",
    "volume_24h",
    "unique_bettor_count",
    "seconds_to_close",
    "seconds_since_creation",
}


def test_features_extracts_point_in_time_values():
    snap = make_snapshot(probability=0.42)
    fv = extract_features(make_market(), snap, NOW)
    assert fv.market_id == "synthetic_m1"
    assert fv.available_at == snap.observed_at
    assert set(fv.values) == EXPECTED_KEYS
    assert fv.values["market_probability"] == 0.42
    assert fv.values["total_liquidity"] == 500.0
    assert fv.values["unique_bettor_count"] == 20
    # Durations are measured at the snapshot time, not at `now`.
    assert fv.values["seconds_to_close"] == pytest.approx(
        (timedelta(days=30, minutes=1)).total_seconds()
    )
    assert fv.values["seconds_since_creation"] == pytest.approx(
        (timedelta(days=10) - timedelta(minutes=1)).total_seconds()
    )


def test_feature_specs_cover_every_key():
    assert {s.name for s in FEATURE_SPECS} == EXPECTED_KEYS


def test_features_reject_future_snapshot():
    snap = make_snapshot(observed_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        extract_features(make_market(), snap, NOW)


def test_features_reject_mismatched_market():
    with pytest.raises(ValueError):
        extract_features(make_market(id="other"), make_snapshot(), NOW)


def test_features_missing_close_is_none():
    fv = extract_features(make_market(close_at=None), make_snapshot(), NOW)
    assert fv.values["seconds_to_close"] is None


def test_features_missing_values_stay_none():
    snap = make_snapshot(total_liquidity=None, volume_24h=None, unique_bettor_count=None)
    fv = extract_features(make_market(), snap, NOW)
    assert fv.values["total_liquidity"] is None
    assert fv.values["volume_24h"] is None
    assert fv.values["unique_bettor_count"] is None

from datetime import UTC, datetime

from ambre.intelligence.context import ContextFeature, FeatureSet
from ambre.intelligence.feature_journal import fingerprint, journal_payload

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _f(name: str, value: float | None, obs: str | None = "o1") -> ContextFeature:
    return ContextFeature(
        name=name,
        value=value,
        unit="C",
        kind="forecast",
        provider_id="open_meteo",
        available_at=T,
        observation_id=obs,
    )


def test_payload_is_sorted_by_name_and_keeps_missing_values_as_none() -> None:
    fs = FeatureSet("m1", (_f("b", None), _f("a", 1.5)))
    payload = journal_payload(fs)
    assert [p["name"] for p in payload] == ["a", "b"]
    assert payload[1]["value"] is None  # absent stays None, never 0
    assert payload[0]["available_at"] == T.isoformat()
    assert payload[0]["provider_id"] == "open_meteo" and payload[0]["observation_id"] == "o1"


def test_empty_feature_set_gives_empty_payload() -> None:
    assert journal_payload(FeatureSet("m1")) == []


def test_fingerprint_ignores_feature_order_but_not_values() -> None:
    a = journal_payload(FeatureSet("m1", (_f("a", 1.0), _f("b", 2.0))))
    b = journal_payload(FeatureSet("m1", (_f("b", 2.0), _f("a", 1.0))))
    c = journal_payload(FeatureSet("m1", (_f("a", 1.0), _f("b", 2.5))))
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)


def test_fingerprint_distinguishes_none_from_zero() -> None:
    zero = journal_payload(FeatureSet("m1", (_f("a", 0.0),)))
    none = journal_payload(FeatureSet("m1", (_f("a", None),)))
    assert fingerprint(zero) != fingerprint(none)


def test_fingerprint_ignores_features_that_only_grow_with_the_clock() -> None:
    def fs(age: float) -> list[dict]:  # type: ignore[type-arg]
        return journal_payload(
            FeatureSet(
                "m1", (_f("weather.forecast.temperature_max", 30.0), _f("x.age_seconds", age))
            )
        )

    assert fingerprint(fs(60.0)) == fingerprint(fs(660.0))  # 10 minutes later, same knowledge
    changed = journal_payload(FeatureSet("m1", (_f("weather.forecast.temperature_max", 31.0),)))
    assert fingerprint(fs(60.0)) != fingerprint(changed)

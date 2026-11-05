"""API registry: configuration, activation rules and capability lookup (pure parsing)."""

from pathlib import Path

import pytest

from ambre.intelligence.models import InfoNeed, ProviderStatus
from ambre.intelligence.registry import Registry, RegistryError, load_registry, parse_registry

SHIPPED = Path(__file__).resolve().parents[2] / "configs" / "providers.yaml"

GOOD = {
    "providers": {
        "synthetic_quakes": {
            "name": "Synthetic quakes",
            "domain": "seismic",
            "status": "VERIFIED_FREE",
            "enabled": True,
            "internal_budget": {"per_hour": 10},
            "capabilities": {"recent_earthquakes": 0.8},
        }
    }
}


def with_changes(**changes: object) -> dict[str, object]:
    entry = dict(GOOD["providers"]["synthetic_quakes"])
    entry.update(changes)
    return {"providers": {"synthetic_quakes": entry}}


def test_parses_a_valid_registry() -> None:
    reg = parse_registry(GOOD)
    assert [c.id for c in reg.enabled()] == ["synthetic_quakes"]
    assert [c.id for c in reg.providers_for(InfoNeed.RECENT_EARTHQUAKES)] == ["synthetic_quakes"]
    assert reg.get("synthetic_quakes").status is ProviderStatus.VERIFIED_FREE
    assert reg.get("nope") is None


def test_a_disabled_provider_is_listed_but_never_offered() -> None:
    reg = parse_registry(with_changes(enabled=False))
    assert reg.get("synthetic_quakes") is not None
    assert reg.enabled() == []
    assert reg.providers_for(InfoNeed.RECENT_EARTHQUAKES) == []


@pytest.mark.parametrize(
    "status", ["UNVERIFIED", "PAID_ONLY", "REQUIRES_REGISTRATION", "UNAVAILABLE"]
)
def test_cannot_enable_a_provider_that_is_not_verified_usable(status: str) -> None:
    with pytest.raises(RegistryError, match="status"):
        parse_registry(with_changes(status=status))


def test_cannot_enable_a_provider_that_needs_a_key() -> None:
    with pytest.raises(RegistryError, match="authentication"):
        parse_registry(with_changes(authentication="api_key"))


def test_cannot_enable_a_provider_without_an_internal_budget() -> None:
    with pytest.raises(RegistryError, match="budget"):
        parse_registry(with_changes(internal_budget={}))


def test_internal_budget_cannot_exceed_the_documented_limit() -> None:
    with pytest.raises(RegistryError, match="exceeds"):
        parse_registry(
            with_changes(provider_limits={"per_hour": 5}, internal_budget={"per_hour": 10})
        )
    ok = parse_registry(
        with_changes(provider_limits={"per_hour": 50}, internal_budget={"per_hour": 10})
    )
    assert ok.get("synthetic_quakes") is not None


def test_min_interval_cannot_be_shorter_than_the_documented_one() -> None:
    with pytest.raises(RegistryError, match="exceeds"):
        parse_registry(
            with_changes(
                provider_limits={"min_interval_seconds": 5},
                internal_budget={"min_interval_seconds": 2},
            )
        )


def test_unknown_keys_are_rejected_so_a_typo_cannot_hide_a_limit() -> None:
    with pytest.raises(RegistryError):
        parse_registry(with_changes(internal_budjet={"per_hour": 1}))


def test_secrets_never_belong_in_the_registry() -> None:
    with pytest.raises(RegistryError):
        parse_registry(with_changes(api_key="abc"))


def test_empty_or_malformed_document() -> None:
    with pytest.raises(RegistryError):
        parse_registry({})
    with pytest.raises(RegistryError):
        parse_registry({"providers": []})


def test_shipped_registry_matches_the_verified_whitelist() -> None:
    reg = load_registry(SHIPPED)
    assert isinstance(reg, Registry)
    ids = {c.id for c in reg.all()}
    owner_confirmed = {
        "usgs_earthquakes",
        "nasa_eonet",
        "open_meteo",
        "gdelt",
        "hacker_news",
        "github",
        "bluesky",
        "manifold",
    }
    # News ingestion (ADR-010 addendum): one verified API, and publisher feeds that exist only
    # as disabled, unverified entries until their terms are checked.
    news_additions = {"spaceflight_news", "bbc_news_world", "guardian_world", "dw_top_stories"}
    assert ids == owner_confirmed | news_additions
    assert {c.id for c in reg.enabled()} & news_additions == {"spaceflight_news"}
    for card in reg.enabled():
        assert card.status in (ProviderStatus.VERIFIED_FREE, ProviderStatus.VERIFIED_LIMITED)
        assert card.authentication == "none"
        assert not card.internal_budget.is_empty
    # open-meteo is non-commercial only: the registry must say so explicitly
    open_meteo = reg.get("open_meteo")
    assert open_meteo is not None and open_meteo.allow_commercial_use is False

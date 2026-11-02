"""API registry: provider cards from configuration, with the rules that decide whether a
provider may be switched on (pure parsing; the file read is the only I/O).

Health and usage live in the database, not here: the registry is configuration.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ambre.intelligence.models import InfoNeed, ProviderCard, ProviderStatus

USABLE = (ProviderStatus.VERIFIED_FREE, ProviderStatus.VERIFIED_LIMITED)
_CAPS = ("per_minute", "per_hour", "per_day")


class RegistryError(ValueError):
    """The registry configuration is invalid or breaks an activation rule."""


class Registry:
    def __init__(self, cards: Mapping[str, ProviderCard]) -> None:
        self._cards = dict(cards)

    def all(self) -> list[ProviderCard]:
        return list(self._cards.values())

    def get(self, provider_id: str) -> ProviderCard | None:
        return self._cards.get(provider_id)

    def enabled(self) -> list[ProviderCard]:
        return [c for c in self._cards.values() if c.enabled]

    def providers_for(self, need: InfoNeed) -> list[ProviderCard]:
        return [c for c in self.enabled() if c.provides(need)]


def _check_budget_within_limits(card: ProviderCard) -> None:
    mine, theirs = card.internal_budget, card.provider_limits
    for attr in _CAPS:
        ours, doc = getattr(mine, attr), getattr(theirs, attr)
        if ours is not None and doc is not None and ours > doc:
            raise RegistryError(f"{card.id}: internal {attr} {ours} exceeds documented {doc}")
    own_i, doc_i = mine.min_interval_seconds, theirs.min_interval_seconds
    if own_i is not None and doc_i is not None and own_i < doc_i:
        raise RegistryError(
            f"{card.id}: internal min_interval {own_i} exceeds the documented rate "
            f"(shorter than {doc_i})"
        )


def _check_activation(card: ProviderCard) -> None:
    if not card.enabled:
        return
    if card.status not in USABLE:
        raise RegistryError(f"{card.id}: cannot enable with status {card.status.value}")
    if card.authentication != "none":
        raise RegistryError(f"{card.id}: cannot enable, authentication {card.authentication!r}")
    if card.internal_budget.is_empty:
        raise RegistryError(f"{card.id}: cannot enable without an internal budget")


def parse_registry(document: Mapping[str, Any]) -> Registry:
    providers = document.get("providers")
    if not isinstance(providers, Mapping) or not providers:
        raise RegistryError("registry needs a non-empty 'providers' mapping")
    cards: dict[str, ProviderCard] = {}
    for provider_id, raw in providers.items():
        if not isinstance(raw, Mapping):
            raise RegistryError(f"{provider_id}: entry must be a mapping")
        try:
            card = ProviderCard.model_validate({**raw, "id": provider_id})
        except ValidationError as exc:
            raise RegistryError(f"{provider_id}: {exc}") from exc
        _check_budget_within_limits(card)
        _check_activation(card)
        cards[provider_id] = card
    return Registry(cards)


def load_registry(path: Path) -> Registry:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise RegistryError(f"{path}: not a mapping")
    return parse_registry(data)

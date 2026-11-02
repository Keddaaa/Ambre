"""Which registry entries have a working connector. A provider can be listed and even enabled
without one: the research pass then records ``SKIPPED_NO_CONNECTOR`` instead of pretending."""

from collections.abc import Callable
from datetime import datetime

import httpx

from ambre.intelligence.models import ProviderCard
from ambre.intelligence.providers.base import Provider
from ambre.intelligence.providers.feed import FeedProvider
from ambre.intelligence.providers.gdelt import GdeltProvider
from ambre.intelligence.providers.open_meteo import OpenMeteoProvider
from ambre.intelligence.providers.spaceflight_news import SpaceflightNewsProvider
from ambre.intelligence.providers.usgs import UsgsProvider
from ambre.intelligence.registry import Registry

ConnectorFactory = Callable[[ProviderCard, httpx.AsyncClient, Callable[[], datetime]], Provider]

CONNECTORS: dict[str, ConnectorFactory] = {
    "usgs_earthquakes": lambda card, http, clock: UsgsProvider(card, http, clock=clock),
    "open_meteo": lambda card, http, clock: OpenMeteoProvider(card, http, clock=clock),
    "gdelt": lambda card, http, clock: GdeltProvider(card, http, clock=clock),
    "spaceflight_news": lambda card, http, clock: SpaceflightNewsProvider(card, http, clock=clock),
    "rss_feed": lambda card, http, clock: FeedProvider(card, http, clock=clock),
}


def build_providers(
    registry: Registry, http: httpx.AsyncClient, clock: Callable[[], datetime]
) -> dict[str, Provider]:
    """Clients for the enabled providers that have a connector."""
    return {
        card.id: CONNECTORS[card.connector or card.id](card, http, clock)
        for card in registry.enabled()
        if (card.connector or card.id) in CONNECTORS
    }

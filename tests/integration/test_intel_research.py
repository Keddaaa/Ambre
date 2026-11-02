"""End-to-end research pass on SQLite with a mocked USGS transport: no Internet, no bets."""

import asyncio
import copy
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from builders import SETTINGS, T_SYNC, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.config.settings import Settings
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, PositionRow
from ambre.intelligence import orm as intel_orm
from ambre.intelligence.models import ProviderCard
from ambre.intelligence.planning import Gazetteer, Region
from ambre.intelligence.providers.usgs import UsgsProvider
from ambre.intelligence.registry import Registry, parse_registry
from ambre.intelligence.service import ResearchReport, research_pass

NOW = T_SYNC + timedelta(minutes=30)
GAZ = Gazetteer([Region("new_zealand", ("new zealand",), (-48.0, -33.0, 165.0, 179.0))])


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def registry(per_day: int = 500, reserve: float = 0.2, enabled: bool = True) -> Registry:
    common = {"domain": "seismic", "status": "VERIFIED_FREE", "freshness_seconds": 600}
    return parse_registry(
        {
            "providers": {
                "usgs_earthquakes": {
                    **common,
                    "name": "USGS",
                    "enabled": enabled,
                    "internal_budget": {"min_interval_seconds": 60, "per_day": per_day},
                    "reserve_fraction": reserve,
                    "capabilities": {"recent_earthquakes": 0.9},
                },
                "other_quakes": {
                    **common,
                    "name": "Other catalogue",
                    "enabled": True,
                    "internal_budget": {"per_day": 100},
                    "capabilities": {"recent_earthquakes": 0.5},
                },
            }
        }
    )


def feature(
    fid: str, mag: float, place: str, lat: float, lon: float, status: str = "reviewed"
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": fid,
        "properties": {
            "mag": mag,
            "place": place,
            "time": ms(NOW - timedelta(days=2)),
            "updated": ms(NOW - timedelta(days=1)),
            "status": status,
        },
        "geometry": {"type": "Point", "coordinates": [lon, lat, 10.0]},
    }


QUAKES = {
    "type": "FeatureCollection",
    "features": [
        feature("synthetic_nz_big", 6.8, "10 km N of Synthetictown, New Zealand", -41.0, 174.0),
        feature(
            "synthetic_nz_small",
            4.7,
            "20 km S of Syntheticville, New Zealand",
            -43.0,
            172.0,
            "automatic",
        ),
        feature("synthetic_chile", 7.1, "somewhere in Chile", -33.5, -70.6),
    ],
}


def market(
    base: dict[str, Any], mid: str, question: str, probability: float = 0.3
) -> dict[str, Any]:
    m = copy.deepcopy(base)
    m.update(
        id=mid,
        question=question,
        slug=mid,
        url=f"https://example.invalid/{mid}",
        probability=probability,
    )
    m["lastUpdatedTime"] = ms(T_SYNC)
    return m


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    assert intel_orm.IntelRequestRow.__tablename__ in Base.metadata.tables
    f = make_session_factory(engine)
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    sync(
        f,
        [
            market(
                base,
                "synthetic_eq_nz",
                "Will there be a 6.5 or greater magnitude earthquake in New Zealand in 2026?",
            ),
            market(
                base,
                "synthetic_eq_death",
                "Will there be an earthquake causing at least 1 death in New Zealand by 2027?",
            ),
            market(base, "synthetic_other", "Will it be sunny in Paris?"),
        ],
        T_SYNC,
    )
    yield f
    engine.dispose()


class Harness:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.calls: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json=QUAKES
        )

    def run(
        self,
        now: datetime = NOW,
        reg: Registry | None = None,
        with_connector: bool = True,
        settings: Settings = SETTINGS,
    ) -> ResearchReport:
        reg = reg or registry()

        def handle(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return self.handler(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        card: ProviderCard | None = reg.get("usgs_earthquakes")
        assert card is not None
        providers = (
            {"usgs_earthquakes": UsgsProvider(card, http, clock=lambda: now)}
            if with_connector
            else {}
        )
        with self.factory() as s:
            report = asyncio.run(
                research_pass(s, settings, now, registry=reg, providers=providers, gazetteer=GAZ)
            )
            s.commit()
        return report


@pytest.fixture
def h(factory: sessionmaker[Session]) -> Harness:
    return Harness(factory)


def count(factory: sessionmaker[Session], model: Any) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def test_one_request_serves_every_earthquake_market_and_links_candidates(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    report = h.run()
    assert len(h.calls) == 1 and report.calls == 1  # three markets, one call, one provider
    (entry,) = report.requests
    assert entry["status"] == "OK" and entry["provider"] == "usgs_earthquakes"
    assert entry["markets"] == ["synthetic_eq_death", "synthetic_eq_nz"]
    q = dict(h.calls[0].url.params)
    assert q["minmagnitude"] == "4.5"  # min(6.5 stated, collection floor 4.5 for the unstated one)

    assert report.new_observations == 3
    with factory() as s:
        links = s.execute(
            select(
                intel_orm.IntelMarketLinkRow.market_id,
                intel_orm.IntelMarketLinkRow.observation_id,
                intel_orm.IntelMarketLinkRow.criteria_status,
                intel_orm.IntelMarketLinkRow.usable_for_prediction,
            )
        ).all()
        by_market: dict[str, int] = {}
        for market_id, _, _, usable in links:
            by_market[market_id] = by_market.get(market_id, 0) + 1
            assert (
                usable is False
            )  # resolution criteria are not read: nothing may inform a prediction
        # nz market: only the 6.8 (>= 6.5) in the region;
        # death market: both NZ quakes, none in Chile
        assert by_market == {"synthetic_eq_nz": 1, "synthetic_eq_death": 2}
        statuses = {r[2] for r in links}
        assert statuses == {"threshold_met_criteria_unread", "threshold_unknown"}


def test_the_pass_never_touches_decisions_positions_or_the_ledger(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    before = [count(factory, m) for m in (DecisionRow, PositionRow, LedgerRow)]
    h.run()
    assert [count(factory, m) for m in (DecisionRow, PositionRow, LedgerRow)] == before


def test_each_request_explains_why_which_source_what_it_costs_and_why_not_the_others(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    with factory() as s:
        row = s.scalars(select(intel_orm.IntelRequestRow)).one()
        e = row.explanation
        assert {w["market_id"] for w in e["why"]} == {
            "synthetic_eq_nz",
            "synthetic_eq_death",
        }  # 1: why
        assert e["routing"]["chosen"] == "usgs_earthquakes"  # 2: which source
        other = next(c for c in e["routing"]["candidates"] if c["provider"] == "other_quakes")
        assert not other["eligible"] and any(
            "lower relevance" in r for r in other["reasons"]
        )  # 3: why not the others
        assert e["cost"] == {
            "provider": "usgs_earthquakes",
            "units": 1,
            "unit": "request",
            "money": 0,
        }  # 4: cost
        assert e["value"]["expected_information_gain"] is None  # a ranking, not an invented gain
        assert any("resolution criteria" in u for u in e["unresolved"])
        assert set(e["links"]) == {"synthetic_eq_nz", "synthetic_eq_death"}  # 6: affected markets
        assert (
            row.usefulness is None
        )  # 8: retrospective usefulness is measured later, never assumed
        assert row.cost_units == 1 and row.observation_count == 3


def test_a_fresh_answer_is_reused_and_not_searched_again(h: Harness) -> None:
    h.run()
    again = h.run(NOW + timedelta(minutes=2))
    assert again.calls == 0 and len(h.calls) == 1
    assert again.requests[0]["status"] == "SKIPPED_FRESH"
    assert "already known" in again.requests[0]["detail"]
    assert set(again.markets_without_information) == {"synthetic_eq_nz", "synthetic_eq_death"}


def test_after_the_answer_expires_it_searches_again_without_duplicating_observations(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    later = h.run(NOW + timedelta(minutes=11))
    assert later.calls == 1 and later.new_observations == 0 and later.new_links == 0
    assert count(factory, intel_orm.IntelObservationRow) == 3
    assert count(factory, intel_orm.IntelUsageRow) == 2  # both calls were counted


def test_the_daily_budget_stops_the_search_and_the_secondary_reserve_is_kept(
    h: Harness,
) -> None:
    tight = registry(per_day=1, reserve=0.2)  # floor(1 * 0.8) = 0 for non-priority work
    report = h.run(reg=tight)
    assert report.calls == 0 and h.calls == []
    entry = report.requests[0]
    # usgs is refused by the budget, so the other provider is chosen; it has no connector here
    assert entry["status"] == "SKIPPED_NO_CONNECTOR"
    usgs = next(
        c
        for c in entry["explanation"]["routing"]["candidates"]
        if c["provider"] == "usgs_earthquakes"
    )
    assert any("reserved for priority" in r for r in usgs["reasons"])


def test_a_disabled_provider_is_never_called(h: Harness) -> None:
    off = parse_registry(
        {
            "providers": {
                "usgs_earthquakes": {
                    "name": "USGS",
                    "domain": "seismic",
                    "status": "VERIFIED_FREE",
                    "enabled": False,
                    "capabilities": {"recent_earthquakes": 0.9},
                }
            }
        }
    )
    report = h.run(reg=off)
    assert h.calls == [] and report.requests[0]["status"] == "SKIPPED_NO_PROVIDER"


def test_a_failed_call_pauses_the_provider_and_the_markets_stay_uninformed(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.handler = lambda r: httpx.Response(429, headers={"Retry-After": "300"})
    failed = h.run()
    assert failed.requests[0]["status"] == "FAILED" and len(h.calls) == 1
    assert set(failed.markets_without_information) == {"synthetic_eq_nz", "synthetic_eq_death"}
    assert count(factory, intel_orm.IntelObservationRow) == 0
    with factory() as s:
        state = s.get(intel_orm.IntelProviderStateRow, "usgs_earthquakes")
        assert state is not None and state.consecutive_failures == 1
        assert state.paused_until is not None
        assert (state.paused_until - NOW).total_seconds() == pytest.approx(
            300.0
        )  # Retry-After wins
    # while paused, no new call is made and the explanation says why
    during = h.run(NOW + timedelta(minutes=2))
    assert len(h.calls) == 1 and during.calls == 0
    assert during.requests[0]["status"] in ("SKIPPED_NO_PROVIDER", "SKIPPED_NO_CONNECTOR")
    # after the pause ends, it recovers and clears the failure counter
    h.handler = lambda r: httpx.Response(200, json=QUAKES)
    recovered = h.run(NOW + timedelta(minutes=10))
    assert recovered.requests[0]["status"] == "OK"
    with factory() as s:
        state = s.get(intel_orm.IntelProviderStateRow, "usgs_earthquakes")
        assert state is not None and state.consecutive_failures == 0 and state.paused_until is None


def test_an_inconclusive_answer_is_not_treated_as_information(h: Harness) -> None:
    h.handler = lambda r: httpx.Response(200, json={})
    report = h.run()
    assert report.requests[0]["status"] == "INCONCLUSIVE"
    assert set(report.markets_without_information) == {"synthetic_eq_nz", "synthetic_eq_death"}


def test_an_empty_answer_is_information_and_is_cached(h: Harness) -> None:
    h.handler = lambda r: httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    first = h.run()
    assert first.requests[0]["status"] == "EMPTY" and first.markets_without_information == []
    assert h.run(NOW + timedelta(minutes=1)).requests[0]["status"] == "SKIPPED_FRESH"


def test_markets_priced_near_certainty_are_not_worth_the_quota(
    factory: sessionmaker[Session],
) -> None:
    from sqlalchemy import update

    from ambre.infrastructure.orm import SnapshotRow

    with factory() as s:
        s.execute(update(SnapshotRow).values(probability=0.001))
        s.commit()
    h = Harness(factory)
    report = h.run()
    assert h.calls == [] and report.requests[0]["status"] == "SKIPPED_LOW_VALUE"
    assert "not worth the quota" in report.requests[0]["detail"]


def test_without_a_matching_market_nothing_is_searched(factory: sessionmaker[Session]) -> None:
    from sqlalchemy import update

    from ambre.infrastructure.orm import MarketRow

    with factory() as s:
        s.execute(update(MarketRow).values(question="Will it be sunny in Paris?"))
        s.commit()
    h = Harness(factory)
    report = h.run()
    assert report.requests == [] and h.calls == []


def test_every_open_market_is_scanned_even_beyond_the_decision_cycle_limit(h: Harness) -> None:
    """The decision cycle looks at a few hundred markets; planning is cheap and must see all."""
    narrow = Settings(
        database_url="sqlite://", execution_mode=SETTINGS.execution_mode, max_markets_per_cycle=1
    )
    report = h.run(settings=narrow)
    assert report.requests[0]["status"] == "OK"
    assert report.requests[0]["markets"] == ["synthetic_eq_death", "synthetic_eq_nz"]

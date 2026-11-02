"""The full journey for a weather market, offline: Manifold market -> understood criteria ->
needed data -> chosen source -> collection -> dated features -> INSUFFICIENT_MODEL.

Open-Meteo is mocked with a hand-written body of the shape seen live on 2026-11-02."""

import asyncio
import copy
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from builders import SETTINGS, T_SYNC, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, MarketRow, PositionRow
from ambre.intelligence import orm as intel_orm
from ambre.intelligence import store
from ambre.intelligence.connectors import build_providers
from ambre.intelligence.estimation import EstimationStatus, estimate_from_context
from ambre.intelligence.generic_planning import PlanStatus, plan_market
from ambre.intelligence.planning import load_gazetteer
from ambre.intelligence.registry import load_registry
from ambre.intelligence.resolution import analyze_market, load_rules
from ambre.intelligence.service import ResearchReport, market_context, research_pass
from ambre.pipeline import to_market

NOW = T_SYNC + timedelta(minutes=30)  # 2026-10-31 14:35 UTC
ROOT = Path(__file__).resolve().parents[2] / "configs"
NOAA_Q = (
    "According to NOAA, will "
    "the high temperature in New York City exceed 90°F on November 10, 2026?"
)
SECOND_Q = (
    "According to NOAA, will "
    "the high temperature in New York City exceed 60°F on November 12, 2026?"
)
LONDON_Q = (
    "According to the Met Office, will the high temperature in London exceed 15°C "
    "on November 6, 2026?"
)
NO_SOURCE_Q = "Will the high temperature in New York City exceed 90°F on November 10, 2026?"
FAR_Q = (
    "According to NOAA, will the high temperature in New York City exceed 90°F on March 3, 2027?"
)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def market(base: dict[str, Any], mid: str, question: str) -> dict[str, Any]:
    m = copy.deepcopy(base)
    m.update(
        id=mid, question=question, slug=mid, url=f"https://example.invalid/{mid}", probability=0.3
    )
    m["lastUpdatedTime"] = ms(T_SYNC)
    m["closeTime"] = ms(datetime(2027, 4, 1, tzinfo=UTC))
    return m


def daily_body(
    lat: float, lon: float, tz: str, start: date, days: int, base_temp: float
) -> dict[str, Any]:
    times = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "timezone": tz,
        "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
        "daily": {"time": times, "temperature_2m_max": [base_temp + i for i in range(days)]},
    }


class Harness:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.calls: list[httpx.Request] = []
        self.registry = load_registry(ROOT / "providers.yaml")
        self.gazetteer = load_gazetteer(ROOT / "gazetteer.yaml")

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        q = dict(request.url.params)
        lat, lon, days = float(q["latitude"]), float(q["longitude"]), int(q["forecast_days"])
        return httpx.Response(200, json=daily_body(lat, lon, q["timezone"], NOW.date(), days, 20.0))

    def run(self, now: datetime = NOW) -> ResearchReport:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        providers = build_providers(self.registry, http, lambda: now)
        with self.factory() as s:
            report = asyncio.run(
                research_pass(
                    s,
                    SETTINGS,
                    now,
                    registry=self.registry,
                    providers=providers,
                    gazetteer=self.gazetteer,
                )
            )
            s.commit()
        return report


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
            market(base, "synthetic_w_nyc10", NOAA_Q),
            market(base, "synthetic_w_nyc12", SECOND_Q),
            market(base, "synthetic_w_london", LONDON_Q),
            market(base, "synthetic_w_nosrc", NO_SOURCE_Q),
            market(base, "synthetic_w_far", FAR_Q),
        ],
        T_SYNC,
    )
    yield f
    engine.dispose()


@pytest.fixture
def h(factory: sessionmaker[Session]) -> Harness:
    return Harness(factory)


def test_the_planner_sorts_every_market_by_what_can_honestly_be_done(h: Harness) -> None:
    report = h.run()
    # nyc10, nyc12, london and nosrc are collectable (collecting needs no source); far is not
    assert report.plans["COLLECTABLE"] == 4 and report.plans["OUT_OF_HORIZON"] == 1
    far = next(x for x in report.not_collectable if x["market_id"] == "synthetic_w_far")
    assert any("forecast horizon is 16 days" in b for b in far["blocking"])


def test_markets_of_one_place_share_a_request_and_other_places_get_their_own(h: Harness) -> None:
    report = h.run()
    assert (
        len(h.calls) == 2 and report.calls == 2
    )  # nyc (3 markets in one call), london; far is not asked
    by_markets = {tuple(r["markets"]): r for r in report.requests}
    nyc = by_markets[("synthetic_w_nosrc", "synthetic_w_nyc10", "synthetic_w_nyc12")]
    assert nyc["status"] == "OK" and nyc["provider"] == "open_meteo"
    assert len(by_markets) == 2
    nyc_call = next(c for c in h.calls if dict(c.url.params)["timezone"] == "America/New_York")
    q = dict(nyc_call.url.params)
    assert q["daily"] == "temperature_2m_max" and q["forecast_days"] == "13"  # up to Nov 12
    assert set(q) == {"latitude", "longitude", "daily", "timezone", "forecast_days"}


def test_the_request_explains_what_the_markets_say_and_why_this_source(h: Harness) -> None:
    report = h.run()
    nyc = next(r for r in report.requests if "synthetic_w_nyc10" in r["markets"])
    e = nyc["explanation"]
    u = next(x for x in e["understanding"] if x["market_id"] == "synthetic_w_nyc10")
    assert u["domain"] == "WEATHER" and u["estimable"] is True and u["blocking"] == []
    assert "> 90°F" in u["yes_condition"] and "<= 90°F" in u["no_condition"]
    assert u["event_period"]["end"] == "2026-11-10" and u["closes_at"].startswith("2027-04-01")
    blocked = next(x for x in e["understanding"] if x["market_id"] == "synthetic_w_nosrc")
    assert blocked["estimable"] is False and any(
        "resolution source" in b for b in blocked["blocking"]
    )
    assert e["routing"]["chosen"] == "open_meteo"
    usgs = next(c for c in e["routing"]["candidates"] if c["provider"] == "usgs_earthquakes")
    assert not usgs["eligible"] and any(
        "does not provide weather_forecast" in r for r in usgs["reasons"]
    )
    assert e["cost"]["money"] == 0 and e["value"]["expected_information_gain"] is None


def test_forecasts_are_stored_dated_and_linked_only_to_the_day_each_market_asks_about(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    with factory() as s:
        links = s.execute(
            select(
                intel_orm.IntelMarketLinkRow.market_id, intel_orm.IntelObservationRow.payload
            ).join(
                intel_orm.IntelObservationRow,
                intel_orm.IntelObservationRow.observation_id
                == intel_orm.IntelMarketLinkRow.observation_id,
            )
        ).all()
        by_market = {m: p["target_date"] for m, p in links if p["variable"] == "temperature_max"}
        assert by_market["synthetic_w_nyc10"] == "2026-11-10"
        assert by_market["synthetic_w_nyc12"] == "2026-11-12"
        assert by_market["synthetic_w_london"] == "2026-11-06"
        assert "synthetic_w_far" not in by_market
        rows = s.scalars(
            select(intel_orm.IntelObservationRow).where(
                intel_orm.IntelObservationRow.provider_id == "open_meteo"
            )
        ).all()
        assert rows and all(
            r.kind == "forecast" and r.available_at == NOW and r.event_at is None for r in rows
        )
        usable = s.scalars(select(intel_orm.IntelMarketLinkRow.usable_for_prediction)).all()
        assert usable and not any(
            usable
        )  # resolution criteria unread: no link informs a prediction


def test_dated_features_then_insufficient_model_never_an_invented_probability(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    rules = load_rules()
    with factory() as s:
        row = s.get(MarketRow, "synthetic_w_nyc10")
        assert row is not None
        market_ = to_market(row)
        analysis = analyze_market(
            market_.id, market_.question, market_.close_at, NOW, h.gazetteer, rules
        )
        plan = plan_market(analysis, h.gazetteer, NOW)
        assert plan.status is PlanStatus.COLLECTABLE and plan.estimable
        features = market_context(s, plan, h.registry, NOW, market_created_at=market_.created_at)
    vals = {f.name: f.value for f in features.features}
    assert vals["weather.forecast.temperature_max"] == 30.0  # day index 10 of the mocked series
    assert vals["weather.forecast.temperature_max.margin"] == pytest.approx(
        30.0 - 32.2222, abs=1e-3
    )
    assert all(f.available_at <= NOW and f.kind == "forecast" for f in features.features)
    result = estimate_from_context(plan, features, [])  # no model has been trained and validated
    assert result.status is EstimationStatus.INSUFFICIENT_MODEL and result.probability is None
    assert "weather.forecast.temperature_max" in result.feature_names


def test_a_market_that_names_no_source_is_collected_but_not_estimable(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    with factory() as s:
        row = s.get(MarketRow, "synthetic_w_nosrc")
        assert row is not None
        m = to_market(row)
        plan = plan_market(
            analyze_market(m.id, m.question, m.close_at, NOW, h.gazetteer, load_rules()),
            h.gazetteer,
            NOW,
        )
        features = market_context(s, plan, h.registry, NOW, market_created_at=m.created_at)
    assert features.features  # the data exists
    result = estimate_from_context(plan, features, [])
    assert result.status is EstimationStatus.NOT_ESTIMABLE and result.probability is None


def test_features_never_include_a_forecast_that_was_not_yet_available(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    earlier = NOW - timedelta(hours=2)  # a decision taken before the collection happened
    with factory() as s:
        row = s.get(MarketRow, "synthetic_w_nyc10")
        assert row is not None
        m = to_market(row)
        plan = plan_market(
            analyze_market(m.id, m.question, m.close_at, earlier, h.gazetteer, load_rules()),
            h.gazetteer,
            earlier,
        )
        features = market_context(s, plan, h.registry, earlier, market_created_at=m.created_at)
    assert features.features == ()
    assert any("after the decision time" in reason for _, reason in features.rejected)


def test_a_second_pass_reuses_fresh_answers_and_makes_no_call(h: Harness) -> None:
    h.run()
    again = h.run(NOW + timedelta(minutes=10))
    assert again.calls == 0 and len(h.calls) == 2
    assert {r["status"] for r in again.requests} == {"SKIPPED_FRESH"}


def test_after_the_forecast_expires_a_new_dated_reading_is_added_not_overwritten(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    with factory() as s:
        before = s.scalar(select(func.count()).select_from(intel_orm.IntelObservationRow)) or 0
    later = h.run(NOW + timedelta(hours=2))
    assert later.calls == 2 and later.new_observations > 0
    with factory() as s:
        after = s.scalar(select(func.count()).select_from(intel_orm.IntelObservationRow)) or 0
        assert after == before + later.new_observations
        assert after > before  # the earlier readings are still there


def test_the_quota_stops_the_weather_calls_when_the_budget_is_spent(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    with factory() as s:
        for i in range(
            20
        ):  # the per-minute internal budget is 20; the reserve leaves 16 to routine work
            store.record_usage(
                s, "open_meteo", NOW - timedelta(seconds=i + 1), purpose="research", request_id=None
            )
        s.commit()
    report = h.run()
    assert report.calls == 0 and h.calls == []
    assert all(r["status"] == "SKIPPED_NO_PROVIDER" for r in report.requests)
    assert all(
        any("quota" in reason for reason in c["reasons"])
        for r in report.requests
        for c in r["explanation"]["routing"]["candidates"]
        if c["provider"] == "open_meteo"
    )


def test_the_whole_journey_never_touches_decisions_positions_or_the_ledger(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    def counts() -> list[int]:
        with factory() as s:
            return [
                s.scalar(select(func.count()).select_from(m)) or 0
                for m in (DecisionRow, PositionRow, LedgerRow)
            ]

    before = counts()
    h.run()
    h.run(NOW + timedelta(hours=2))
    assert counts() == before
    with factory() as s:
        assert repo.get_current_simulation(s) is None  # no simulation was even created


def test_feature_journal_records_dated_features_with_the_price_and_never_twice(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    with factory() as s:
        rows = s.scalars(select(intel_orm.IntelFeatureJournalRow)).all()
        by_market = {r.market_id: r for r in rows}
        # markets with features are journaled; the out-of-horizon one has none and is absent
        assert {"synthetic_w_nyc10", "synthetic_w_nyc12", "synthetic_w_london"} <= set(by_market)
        assert "synthetic_w_far" not in by_market
        nyc = by_market["synthetic_w_nyc10"]
        assert nyc.as_of == NOW and nyc.market_probability == pytest.approx(0.3)
        assert nyc.domain == "WEATHER" and nyc.estimable is True
        names = {f["name"]: f for f in nyc.features}
        assert names["weather.forecast.temperature_max"]["value"] == 30.0
        assert all(f["available_at"] <= NOW.isoformat() for f in nyc.features)
        assert by_market["synthetic_w_nosrc"].estimable is False  # collected, not estimable
        first = len(rows)
    h.run()  # same knowledge again: nothing new to record
    with factory() as s:
        assert len(s.scalars(select(intel_orm.IntelFeatureJournalRow)).all()) == first


def test_feature_journal_does_not_touch_decisions_or_the_ledger(
    h: Harness, factory: sessionmaker[Session]
) -> None:
    h.run()
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(DecisionRow)) == 0
        assert s.scalar(select(func.count()).select_from(LedgerRow)) == 0
        assert s.scalar(select(func.count()).select_from(PositionRow)) == 0

"""Universal research: markets with no specialised connector are searched through the generalist
engine, every market keeps an explicit information state, nothing is dropped. Offline: GDELT, USGS
and Open-Meteo are mocked with hand-written bodies of the shapes seen live on 2026-11-02."""

import asyncio
import copy
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from builders import SETTINGS, T_SYNC, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.cognitive.orm import CognitiveNoteRow
from ambre.config.settings import IntelligenceConfig, Settings
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, MarketRow, PositionRow
from ambre.intelligence import store
from ambre.intelligence.connectors import build_providers
from ambre.intelligence.coverage import coverage_report
from ambre.intelligence.estimation import EstimationStatus, estimate_from_context
from ambre.intelligence.generic_planning import plan_market
from ambre.intelligence.orm import IntelMarketLinkRow, IntelMarketStateRow, IntelRequestRow
from ambre.intelligence.planning import load_gazetteer
from ambre.intelligence.registry import Registry, parse_registry
from ambre.intelligence.resolution import analyze_market, load_rules
from ambre.intelligence.service import ResearchReport, market_context, research_pass
from ambre.pipeline import to_market

NOW = T_SYNC + timedelta(minutes=30)
CONFIGS = Path(__file__).resolve().parents[2] / "configs"
OPENAI_RELEASE = "Will OpenAI release a new model before 2027?"
OPENAI_ANNOUNCE = "Will OpenAI announce a model by December 31, 2026?"
ECONOMY = "According to BLS, will the CPI inflation rate exceed 4% in 2027?"
SPORT = "Will the Lakers win the NBA championship in 2027?"
GEOPOLITICS = "Will Ukraine cede land to russia?"
WEATHER_OK = (
    "According to NOAA, will the high temperature in New York City exceed 90°F "
    "on November 10, 2026?"
)
WEATHER_BAD = "Will the high temperature in New York City exceed 90°F?"


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def market(base: dict[str, Any], mid: str, question: str, p: float = 0.3) -> dict[str, Any]:
    m = copy.deepcopy(base)
    m.update(
        id=mid, question=question, slug=mid, url=f"https://example.invalid/{mid}", probability=p
    )
    m["lastUpdatedTime"] = ms(T_SYNC)
    m["closeTime"] = ms(datetime(2027, 4, 1, tzinfo=UTC))
    return m


def article(title: str, domain: str, url: str | None = None) -> dict[str, Any]:
    return {
        "url": url or f"https://{domain}/{abs(hash(title)) % 10**8}",
        "title": title,
        "seendate": "20261031T120000Z",
        "domain": domain,
        "language": "English",
        "sourcecountry": "United States",
    }


def articles(*items: dict[str, Any]) -> dict[str, Any]:
    return {"articles": list(items)}


class Net:
    """All three providers behind one mocked transport; each can be scripted."""

    def __init__(self) -> None:
        self.gdelt: list[httpx.Request] = []
        self.usgs: list[httpx.Request] = []
        self.weather: list[httpx.Request] = []
        self.gdelt_handler: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json={"articles": []}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.gdeltproject.org":
            self.gdelt.append(request)
            return self.gdelt_handler(request)
        if host == "earthquake.usgs.gov":
            self.usgs.append(request)
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        self.weather.append(request)
        q = dict(request.url.params)
        days = int(q["forecast_days"])
        times = [(NOW.date() + timedelta(days=i)).isoformat() for i in range(days)]
        body = {
            "latitude": float(q["latitude"]),
            "longitude": float(q["longitude"]),
            "timezone": q["timezone"],
            "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
            "daily": {"time": times, "temperature_2m_max": [20.0 + i for i in range(days)]},
        }
        return httpx.Response(200, json=body)


def registry(*, gdelt: bool = True) -> Registry:
    doc = yaml.safe_load((CONFIGS / "providers.yaml").read_text(encoding="utf-8"))
    doc["providers"]["gdelt"]["enabled"] = gdelt
    return parse_registry(doc)


class Harness:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.net = Net()
        self.sleeps: list[float] = []
        self.gazetteer = load_gazetteer(CONFIGS / "gazetteer.yaml")

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def run(
        self,
        now: datetime = NOW,
        reg: Registry | None = None,
        settings: Settings = SETTINGS,
    ) -> ResearchReport:
        reg = reg or registry()
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.net.handle))
        providers = build_providers(reg, http, lambda: now)
        with self.factory() as s:
            report = asyncio.run(
                research_pass(
                    s, settings, now, registry=reg, providers=providers,
                    gazetteer=self.gazetteer, sleep=self._sleep,
                )
            )  # fmt: skip
            s.commit()
        return report

    def states(self) -> dict[str, tuple[str, str]]:
        with self.factory() as s:
            return {
                r.market_id: (r.state, r.reason) for r in s.scalars(select(IntelMarketStateRow))
            }


def make_factory(
    tmp_path: Path, questions: dict[str, str], p: float = 0.3
) -> sessionmaker[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'u.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    sync(factory, [market(base, mid, q, p) for mid, q in questions.items()], T_SYNC)
    return factory


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    return tmp_path


def harness(tmp: Path, questions: dict[str, str], p: float = 0.3) -> Iterator[Harness]:
    yield Harness(make_factory(tmp, questions, p))


@pytest.fixture
def tech(tmp: Path) -> Iterator[Harness]:
    yield from harness(tmp, {"synthetic_tech": OPENAI_RELEASE})


def count(h: Harness, model: type) -> int:
    with h.factory() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


# --------------------------------------------------------------- journeys: technology, economy


def test_a_technology_market_with_no_specialised_api_is_searched_and_gets_dated_evidence(
    tech: Harness,
) -> None:
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200,
        json=articles(
            article("OpenAI releases a new model for developers", "news-a.example"),
            article("OpenAI to release GPT-6 next month", "news-b.example"),
            article("Anthropic launches a new model", "news-c.example"),
        ),
    )
    report = tech.run()
    (request,) = report.requests
    assert request["status"] == "OK" and request["provider"] == "gdelt"
    (call,) = tech.net.gdelt
    q = dict(call.url.params)
    assert q["query"] == "OpenAI release model" and q["mode"] == "ArtList"
    assert q["format"] == "json" and q["timespan"] == "1week" and q["maxrecords"] == "75"
    e = request["explanation"]
    assert e["plan"]["level"] == 2 and e["plan"]["query"] == "OpenAI release model"
    assert e["routing"]["chosen"] == "gdelt" and e["cost"]["money"] == 0
    assert any("is not queried directly" not in u for u in e["unresolved"])  # no source was named
    state, reason = tech.states()["synthetic_tech"]
    assert state == "EVIDENCE_FOUND" and "still leaves things open" in reason
    with tech.factory() as s:
        links = s.scalars(select(IntelMarketLinkRow)).all()
        assert len(links) == 2 and {x.relation for x in links} == {"news_mention"}
        assert not any(x.usable_for_prediction for x in links)  # evidence, never a signal
        note = s.scalars(select(CognitiveNoteRow)).one()
        assert note.type == "EXPERIENCE" and "OpenAI release model" in note.content
        assert "gdelt" in note.content and note.provenance["source_refs"][0].startswith(
            "intel_request:"
        )


def test_a_technology_markets_evidence_becomes_dated_features_but_never_an_estimate(
    tech: Harness,
) -> None:
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200,
        json=articles(
            article("OpenAI releases a new model for developers", "news-a.example"),
            article("OpenAI to release GPT-6 next month", "news-b.example"),
        ),
    )
    tech.run()
    reg = registry()
    with tech.factory() as s:
        row = s.get(MarketRow, "synthetic_tech")
        assert row is not None
        m = to_market(row)
        plan = plan_market(
            analyze_market(m.id, m.question, m.close_at, NOW, tech.gazetteer, load_rules()),
            tech.gazetteer,
            NOW,
        )
        features = market_context(s, plan, reg, NOW, market_created_at=m.created_at)
    vals = {f.name: f.value for f in features.features}
    assert vals["news.article_count"] == 2.0 and vals["news.independent_sources"] == 2.0
    assert all(f.available_at <= NOW for f in features.features)
    result = estimate_from_context(plan, features, [])
    assert result.status is EstimationStatus.NOT_ESTIMABLE and result.probability is None


def test_an_economic_market_with_a_named_official_source_is_understood_and_searched(
    tmp: Path,
) -> None:
    h = next(harness(tmp, {"synthetic_eco": ECONOMY}))
    h.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("BLS reports inflation rate at 3.2% in October", "wire.example"))
    )
    report = h.run()
    (request,) = report.requests
    u = request["explanation"]["understanding"][0]
    assert u["domain"] == "ECONOMY" and u["reference_sources"] == ["BLS"]
    assert "> 4%" in u["yes_condition"] and u["event_period"]["end"] == "2027-12-31"
    assert any(
        "named source BLS is not queried directly" in x
        for x in request["explanation"]["unresolved"]
    )
    assert dict(h.net.gdelt[0].url.params)["query"].startswith("BLS inflation")
    assert h.states()["synthetic_eco"][0] == "EVIDENCE_FOUND"


# --------------------------------------------------------------- the other cases of the brief


def test_a_sport_market_with_no_data_is_deferred_not_closed_and_is_retried(tmp: Path) -> None:
    h = next(harness(tmp, {"synthetic_sport": SPORT}))
    h.net.gdelt_handler = lambda r: httpx.Response(200, json={})  # GDELT's ambiguous empty object
    first = h.run()
    assert first.requests[0]["status"] == "INCONCLUSIVE"
    assert h.states()["synthetic_sport"][0] == "RESEARCH_DEFERRED"
    h.net.gdelt_handler = lambda r: httpx.Response(
        200,
        json=articles(
            article("Lakers eye the NBA championship after a strong start", "sports.example")
        ),
    )
    second = h.run(NOW + timedelta(minutes=2))
    assert (
        second.requests[0]["status"] == "OK" and len(h.net.gdelt) == 2
    )  # an inconclusive answer is not cached
    assert h.states()["synthetic_sport"][0] == "EVIDENCE_FOUND"


def test_an_empty_answer_is_insufficient_evidence_and_is_remembered(tmp: Path) -> None:
    h = next(harness(tmp, {"synthetic_sport": SPORT}))
    h.net.gdelt_handler = lambda r: httpx.Response(200, json={"articles": []})
    h.run()
    assert h.states()["synthetic_sport"][0] == "INSUFFICIENT_EVIDENCE"
    again = h.run(NOW + timedelta(hours=1))
    assert again.requests[0]["status"] == "SKIPPED_FRESH" and len(h.net.gdelt) == 1
    assert h.states()["synthetic_sport"][0] == "INSUFFICIENT_EVIDENCE"


def test_weather_with_complete_parameters_reaches_insufficient_model_not_a_prediction(
    tmp: Path,
) -> None:
    h = next(harness(tmp, {"synthetic_w_ok": WEATHER_OK}))
    h.run()
    assert len(h.net.weather) == 1 and h.net.gdelt == []  # the specialised connector served it
    state, reason = h.states()["synthetic_w_ok"]
    assert state == "INSUFFICIENT_MODEL" and "no validated model" in reason


def test_weather_with_missing_parameters_is_understood_and_not_searched(tmp: Path) -> None:
    h = next(harness(tmp, {"synthetic_w_bad": WEATHER_BAD}))
    h.run()
    assert h.net.weather == [] and h.net.gdelt == []
    state, reason = h.states()["synthetic_w_bad"]
    assert state == "UNDERSTOOD" and "event date not stated" in reason


def test_an_ambiguous_geopolitical_market_is_still_searched_but_never_estimable(tmp: Path) -> None:
    h = next(harness(tmp, {"synthetic_geo": GEOPOLITICS}))
    h.net.gdelt_handler = lambda r: httpx.Response(
        200,
        json=articles(article("Ukraine rejects proposal to cede land to Russia", "wire.example")),
    )
    h.run()
    state, _ = h.states()["synthetic_geo"]
    assert state == "EVIDENCE_FOUND"
    with h.factory() as s:
        row = s.get(IntelMarketStateRow, "synthetic_geo")
        assert row is not None and row.estimable is False and row.level == 2


def test_two_markets_about_one_entity_share_one_search_but_keep_their_own_relevance(
    tmp: Path,
) -> None:
    h = next(harness(tmp, {"synthetic_a": OPENAI_RELEASE, "synthetic_b": OPENAI_ANNOUNCE}))
    h.net.gdelt_handler = lambda r: httpx.Response(
        200,
        json=articles(
            article("OpenAI releases a new model", "a.example"),
            article("OpenAI announces new product plans", "b.example"),
        ),
    )
    report = h.run()
    assert len(h.net.gdelt) == 1 and report.calls == 1  # one question asked once
    assert dict(h.net.gdelt[0].url.params)["query"] == "OpenAI model"
    (request,) = report.requests
    assert request["markets"] == ["synthetic_a", "synthetic_b"]
    with h.factory() as s:
        per_market: dict[str, int] = {}
        for link in s.scalars(select(IntelMarketLinkRow)):
            per_market[link.market_id] = per_market.get(link.market_id, 0) + 1
    # the evidence is shared; relevance is judged per market, so each has its own links
    assert per_market == {"synthetic_a": 1, "synthetic_b": 2}


def test_a_disabled_provider_is_not_available_and_is_never_called(tech: Harness) -> None:
    report = tech.run(reg=registry(gdelt=False))
    assert tech.net.gdelt == [] and report.requests == []  # no noise rows for an unavailable source
    state, reason = tech.states()["synthetic_tech"]
    assert state == "NO_AVAILABLE_SOURCE" and "no enabled provider" in reason


def test_an_exhausted_quota_defers_the_market_and_says_so(tech: Harness) -> None:
    with tech.factory() as s:
        for i in range(240):  # the hourly budget (300) minus the 20% reserve for priority work
            store.record_usage(
                s, "gdelt", NOW - timedelta(seconds=15 + i), purpose="research", request_id=None
            )
        s.commit()
    report = tech.run()
    assert tech.net.gdelt == [] and report.calls == 0
    assert report.requests == []  # one provider-wide reason, not a row per topic
    assert "quota" in report.provider_unavailable["gdelt"]
    assert tech.states()["synthetic_tech"][0] == "RESEARCH_DEFERRED"


def test_stale_information_is_searched_again_and_fresh_information_is_not(tech: Harness) -> None:
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("OpenAI releases a new model", "a.example"))
    )
    tech.run()
    soon = tech.run(NOW + timedelta(hours=1))
    assert soon.requests[0]["status"] == "SKIPPED_FRESH" and len(tech.net.gdelt) == 1
    assert tech.states()["synthetic_tech"][0] == "EVIDENCE_FOUND"  # fresh evidence stays on file
    later = tech.run(NOW + timedelta(hours=7))
    assert later.requests[0]["status"] == "OK" and len(tech.net.gdelt) == 2


def test_a_failed_search_is_retried_after_the_pause_and_never_closes_the_market(
    tech: Harness,
) -> None:
    tech.net.gdelt_handler = lambda r: httpx.Response(503)
    tech.run()
    assert tech.states()["synthetic_tech"][0] == "RESEARCH_FAILED"
    paused = tech.run(NOW + timedelta(seconds=20))
    assert len(tech.net.gdelt) == 1  # the provider is paused: no hammering
    assert tech.states()["synthetic_tech"][0] == "RESEARCH_DEFERRED"
    assert paused.requests == [] and "paused" in paused.provider_unavailable["gdelt"]
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("OpenAI releases a new model", "a.example"))
    )
    tech.run(NOW + timedelta(minutes=10))
    assert len(tech.net.gdelt) == 2
    assert tech.states()["synthetic_tech"][0] == "EVIDENCE_FOUND"


def test_old_evidence_stays_visible_while_a_refresh_is_deferred(tech: Harness) -> None:
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("OpenAI releases a new model", "a.example"))
    )
    tech.run()
    tech.net.gdelt_handler = lambda r: httpx.Response(503)
    tech.run(NOW + timedelta(hours=7))  # stale, asked again, failed
    state, reason = tech.states()["synthetic_tech"]
    assert state == "EVIDENCE_FOUND" and "evidence on file" in reason


# ------------------------------------------------------------------------------ queue and pacing


def test_more_groups_than_the_pass_limit_are_queued_paced_and_all_eventually_searched(
    tmp: Path,
) -> None:
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]
    questions = {
        f"synthetic_q{i}": f"Will {n}Corp release a new model before 2027?"
        for i, n in enumerate(names)
    }
    h = next(harness(tmp, questions))
    h.net.gdelt_handler = lambda r: httpx.Response(200, json={"articles": []})
    limited = Settings(
        database_url="sqlite://", execution_mode=SETTINGS.execution_mode,
        intelligence=IntelligenceConfig(max_calls_per_pass=3),
    )  # fmt: skip
    first = h.run(settings=limited)
    assert first.calls == 3 and first.queued_groups == 5
    assert len(h.sleeps) == 2 and all(10 < s <= 12 for s in h.sleeps)  # waited for GDELT's interval
    assert first.waited_seconds == pytest.approx(sum(h.sleeps))
    st = [s for s, _ in h.states().values()]
    assert st.count("INSUFFICIENT_EVIDENCE") == 3 and st.count("RESEARCH_DEFERRED") == 5
    assert sum(first.states.values()) == 8  # every scanned market is in exactly one state
    second = h.run(NOW + timedelta(minutes=1), settings=limited)
    assert second.calls == 3 and second.queued_groups == 2
    third = h.run(NOW + timedelta(minutes=2), settings=limited)
    assert third.calls == 2 and third.queued_groups == 0
    assert len(h.net.gdelt) == 8  # every group asked exactly once: none dropped, none repeated
    assert {s for s, _ in h.states().values()} == {"INSUFFICIENT_EVIDENCE"}


def test_the_queue_serves_the_most_uncertain_markets_first(tmp: Path) -> None:
    h = next(
        harness(tmp, {"synthetic_sure": "Will SureCorp release a new model before 2027?"}, p=0.97)
    )
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
    )
    sync(
        h.factory,
        [market(base, "synthetic_open", "Will OpenCorp release a new model before 2027?", 0.5)],
        T_SYNC,
    )
    limited = Settings(
        database_url="sqlite://", execution_mode=SETTINGS.execution_mode,
        intelligence=IntelligenceConfig(max_calls_per_pass=1, min_value_score=0.0),
    )  # fmt: skip
    h.run(settings=limited)
    assert "OpenCorp" in dict(h.net.gdelt[0].url.params)["query"]  # the 50% market, not the 97% one


# ------------------------------------------------------------------------- coverage and safety


def test_coverage_reconciles_to_the_total_with_denominators_and_no_vanishing_market(
    tmp: Path,
) -> None:
    h = next(
        harness(
            tmp,
            {
                "synthetic_tech": OPENAI_RELEASE,
                "synthetic_eco": ECONOMY,
                "synthetic_w_ok": WEATHER_OK,
                "synthetic_w_bad": WEATHER_BAD,
                "synthetic_cat": "Will my cat like the new food?",
            },
        )
    )
    h.net.gdelt_handler = lambda r: httpx.Response(
        200,
        json=articles(
            article("OpenAI releases a new model", "a.example"),
            article("BLS reports inflation rate at 3.2%", "b.example"),
        ),
    )
    h.run()
    base = next(
        m for m in load_fixture() if m.get("outcomeType") == "BINARY" and not m.get("isResolved")
    )
    sync(
        h.factory,
        [market(base, "synthetic_late", "Will LateCorp release a model before 2027?")],
        T_SYNC,
    )
    with h.factory() as s:
        r = coverage_report(s)
    assert r["total_unresolved_markets"] == 6 and r["reconciled"] is True
    assert sum(r["by_state"].values()) == 6
    assert r["by_state"]["NOT_ANALYZED"] == 1  # the market added after the pass: counted, not lost
    assert r["by_state"]["NO_AVAILABLE_SOURCE"] == 1  # the cat: nothing to search
    assert r["by_state"]["UNDERSTOOD"] == 1  # weather without a date
    assert r["by_state"]["INSUFFICIENT_MODEL"] == 1  # weather with everything but a model
    assert r["by_state"]["EVIDENCE_FOUND"] == 2  # tech + economy
    for ratio in r["ratios"]:
        assert ratio["denominator"] is not None and ratio["of"]
        assert ratio["numerator"] <= ratio["denominator"] or ratio["denominator"] == 0
    assert r["counts"]["with_validated_model"] == 0
    assert r["cost"]["money"] == 0 and r["cost"]["total_requests"] == len(h.net.gdelt) + len(
        h.net.weather
    ) + len(h.net.usgs)
    assert sum(r["by_level"].values()) == 5


def test_research_never_touches_decisions_positions_or_the_ledger(tech: Harness) -> None:
    before = [count(tech, m) for m in (DecisionRow, PositionRow, LedgerRow)]
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("OpenAI releases a new model", "a.example"))
    )
    tech.run()
    tech.run(NOW + timedelta(hours=7))
    assert [count(tech, m) for m in (DecisionRow, PositionRow, LedgerRow)] == before


def test_a_vault_failure_never_stops_the_research(
    tech: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ambre.cognitive import vault

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("vault down")

    monkeypatch.setattr(vault, "create_note", broken)
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("OpenAI releases a new model", "a.example"))
    )
    report = tech.run()
    assert report.requests[0]["status"] == "OK"
    assert tech.states()["synthetic_tech"][0] == "EVIDENCE_FOUND"


def test_every_search_leaves_a_request_row_with_its_cost_and_its_refused_sources(
    tech: Harness,
) -> None:
    tech.net.gdelt_handler = lambda r: httpx.Response(
        200, json=articles(article("OpenAI releases a new model", "a.example"))
    )
    tech.run()
    with tech.factory() as s:
        row = s.scalars(select(IntelRequestRow)).one()
        assert row.cost_units == 1 and row.provider_id == "gdelt" and row.need == "general_news"
        refused = {
            c["provider"]: c["reasons"]
            for c in row.explanation["routing"]["candidates"]
            if not c["eligible"]
        }
        assert "usgs_earthquakes" in refused and any(
            "does not provide general_news" in r for r in refused["usgs_earthquakes"]
        )


def test_a_paused_provider_does_not_flood_the_memory_with_one_row_per_topic(tmp: Path) -> None:
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    questions = {
        f"synthetic_p{i}": f"Will {n}Corp release a new model before 2027?"
        for i, n in enumerate(names)
    }
    h = next(harness(tmp, questions))
    h.net.gdelt_handler = lambda r: httpx.Response(
        429, text="Please limit requests to one every 5 seconds"
    )
    first = h.run()
    assert first.calls == 1  # the first failure pauses the provider; the other groups wait
    assert [r["status"] for r in first.requests] == ["FAILED"]
    assert count(h, IntelRequestRow) == 1  # one row: the call that was really made
    states = [s for s, _ in h.states().values()]
    assert states.count("RESEARCH_DEFERRED") == 4 and states.count("RESEARCH_FAILED") == 1

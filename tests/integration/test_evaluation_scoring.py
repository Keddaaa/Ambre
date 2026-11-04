"""Scoring of resolved markets: repository query, pipeline report, API route and CLI."""

import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from builders import SETTINGS, T_CYCLE, T_SYNC, cycle, sync
from fakes import load_fixture
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ambre import cli
from ambre.api.app import create_app
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, MarketRow
from ambre.pipeline import evaluation_for_simulation


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def populated(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    sync(factory, load_fixture(), T_SYNC)
    cycle(factory)
    return factory


def _decided_markets(session: Session) -> list[str]:
    stmt = select(DecisionRow.market_id).distinct().order_by(DecisionRow.market_id)
    return list(session.scalars(stmt))


def _sim_id(session: Session) -> str:
    sim = repo.get_current_simulation(session)
    assert sim is not None
    return sim.id


def _resolve(session: Session, market_id: str, resolution: str, at) -> None:  # type: ignore[no-untyped-def]
    m = session.get(MarketRow, market_id)
    assert m is not None
    m.is_resolved = True
    m.resolution = resolution
    m.resolution_at = at
    session.flush()


def test_no_resolved_market_gives_empty_report(populated: sessionmaker[Session]) -> None:
    with populated() as s:
        report = evaluation_for_simulation(s, _sim_id(s))
    assert report.n == 0
    assert report.model_brier is None
    assert report.sufficient_sample is False


def test_only_leak_free_yes_no_resolutions_are_scored(populated: sessionmaker[Session]) -> None:
    with populated() as s:
        a, b, c, d, *_ = _decided_markets(s)
        after = T_CYCLE + timedelta(hours=1)
        before = T_CYCLE - timedelta(hours=1)
        _resolve(s, a, "YES", after)  # counted
        _resolve(s, b, "NO", after)  # counted
        _resolve(s, c, "CANCEL", after)  # not a YES/NO outcome
        _resolve(s, d, "YES", before)  # resolved before the decision: possible leak
        s.commit()
        rows = repo.resolved_decision_scores(s, _sim_id(s))
        assert sorted(r[2] for r in rows) == ["NO", "YES"]
        assert len({r[0] for r in rows}) >= 1
        report = evaluation_for_simulation(s, _sim_id(s))
    assert report.n == 2
    assert report.model_brier is not None and report.market_brier is not None
    assert report.sufficient_sample is False


def test_unknown_resolution_time_is_excluded(populated: sessionmaker[Session]) -> None:
    with populated() as s:
        a, *_ = _decided_markets(s)
        _resolve(s, a, "YES", None)
        s.commit()
        assert repo.resolved_decision_scores(s, _sim_id(s)) == []


def test_api_evaluation_route(populated: sessionmaker[Session]) -> None:
    with populated() as s:
        a, *_ = _decided_markets(s)
        _resolve(s, a, "YES", T_CYCLE + timedelta(hours=1))
        s.commit()
    body = (
        TestClient(create_app(SETTINGS, populated), base_url="http://localhost")
        .get("/evaluation")
        .json()
    )
    assert body["n"] == 1
    assert body["sufficient_sample"] is False
    assert body["brier_difference_ci95"] is None
    assert body["model_calibration"][0]["n"] == 1


def test_api_evaluation_without_simulation_is_404(factory: sessionmaker[Session]) -> None:
    r = TestClient(create_app(SETTINGS, factory), base_url="http://localhost").get("/evaluation")
    assert r.status_code == 404


def test_cli_evaluate_prints_json(
    populated: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    out = cli.evaluate(populated)
    assert out is not None
    assert json.loads(json.dumps(out))["n"] == 0
    capsys.readouterr()


# --------------------------------------------------------------------------- backtest


def test_backtest_markets_use_first_snapshot_and_yes_no_only(
    populated: sessionmaker[Session],
) -> None:
    with populated() as s:
        baseline = {r.market_id for r in repo.backtest_markets(s)}
        fresh = [m for m in _decided_markets(s) if m not in baseline]
        a, b = fresh[:2]
        after = T_SYNC + timedelta(days=3)
        _resolve(s, a, "YES", after)
        _resolve(s, b, "CANCEL", after)
        s.commit()
        rows = {r.market_id: r for r in repo.backtest_markets(s)}
    assert a in rows and b not in rows
    assert rows[a].outcome is True
    assert rows[a].decision_at == T_SYNC
    assert 0.0 <= rows[a].market_probability <= 1.0


def test_backtest_markets_skip_unknown_resolution_time(populated: sessionmaker[Session]) -> None:
    with populated() as s:
        baseline = {r.market_id for r in repo.backtest_markets(s)}
        a = next(m for m in _decided_markets(s) if m not in baseline)
        _resolve(s, a, "YES", None)
        s.commit()
        assert a not in {r.market_id for r in repo.backtest_markets(s)}


def test_cli_backtest_prints_json(
    populated: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    with populated() as s:
        baseline = {r.market_id for r in repo.backtest_markets(s)}
        fresh = [m for m in _decided_markets(s) if m not in baseline][:2]
        for market_id in fresh:
            _resolve(s, market_id, "YES", T_SYNC + timedelta(days=3))
        s.commit()
        expected = sum(
            1
            for r in repo.backtest_markets(s)
            if r.resolution_at is not None and r.decision_at < r.resolution_at
        )
    out = cli.backtest(populated)
    assert json.loads(json.dumps(out, default=str))["report"]["n"] == expected
    assert expected >= 2
    assert out["report"]["sufficient_sample"] is False
    capsys.readouterr()

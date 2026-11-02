"""The recalibrated-price model through the real paper cycle: window, journal, risk, safety."""

import copy
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from builders import T_CYCLE, T_SYNC, cycle, sync
from fakes import load_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.config.settings import PredictionConfig, RecalibrationConfig, RiskConfig, Settings
from ambre.domain.models import ExecutionMode
from ambre.execution_engine.executor import ExecutionModeNotAvailable
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, LedgerRow, PositionRow
from ambre.prediction_engine.calibration import PlattCalibrator

A, B = -0.318, 0.937
RECAL = RecalibrationConfig(
    a=A, b=B, n_fit=4474, fitted_on="test", min_age_days=2.0, max_age_days=4.0, uncertainty=0.03
)
FORWARD = Settings(
    database_url="sqlite://",
    execution_mode=ExecutionMode.PAPER,
    prediction=PredictionConfig(model="recalibrated_price", recalibration=RECAL),
    risk=RiskConfig(fee_rate=0.02),
)
DEFAULT = Settings(database_url="sqlite://", execution_mode=ExecutionMode.PAPER)


def ms(at: Any) -> int:
    return int(at.timestamp() * 1000)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    f = make_session_factory(engine)
    base = next(
        m
        for m in load_fixture()
        if m.get("outcomeType") == "BINARY"
        and m.get("mechanism") == "cpmm-1"
        and not m.get("isResolved")
        and (m.get("totalLiquidity") or 0) >= 1000
        and (m.get("uniqueBettorCount") or 0) >= 5
    )
    markets = []
    for mid, age_days in (("synthetic_in", 3.0), ("synthetic_young", 0.5), ("synthetic_old", 20.0)):
        m = copy.deepcopy(base)
        m.update(
            id=mid,
            slug=mid,
            url=f"https://example.invalid/{mid}",
            question=f"Will {mid} happen?",
            createdTime=ms(T_CYCLE - timedelta(days=age_days)),
            closeTime=ms(T_CYCLE + timedelta(days=60)),
            lastUpdatedTime=ms(T_SYNC),
        )
        markets.append(m)
    sync(f, markets, T_SYNC)
    yield f
    engine.dispose()


def decisions(f: sessionmaker[Session]) -> dict[str, DecisionRow]:
    with f() as s:
        return {d.market_id: d for d in s.scalars(select(DecisionRow))}


def test_only_the_market_inside_the_age_window_gets_a_shifted_estimate(
    factory: sessionmaker[Session],
) -> None:
    report = cycle(factory, T_CYCLE, FORWARD)
    assert report.model_version is not None and "recalibrated-price" in report.model_version
    by_market = decisions(factory)
    inside = by_market["synthetic_in"]
    q = inside.market_probability
    expected = PlattCalibrator(A, B, 4474).predict(q)
    assert inside.estimated_probability == pytest.approx(expected, abs=1e-9)
    assert inside.model_version.startswith("recalibrated-price")
    for outside in ("synthetic_young", "synthetic_old"):
        d = by_market[outside]
        assert d.estimated_probability == d.market_probability  # echo: no edge, no trade
        assert d.action == "NO_TRADE" and d.stake == 0.0


def test_a_bet_in_the_window_goes_against_a_price_that_is_too_high_and_pays_the_fee(
    factory: sessionmaker[Session],
) -> None:
    cycle(factory, T_CYCLE, FORWARD)
    d = decisions(factory)["synthetic_in"]
    if d.action == "NO_TRADE":
        pytest.skip("the fixture price leaves no edge after the uncertainty penalty")
    assert d.action == "NO"  # the model lowers the probability of YES
    assert d.estimated_probability < d.market_probability
    assert d.conservative_probability is not None
    assert d.stake <= 0.02 * 1000.0 + 1e-9  # the position cap applies to the stake
    with factory() as s:
        assert (s.scalar(select(func.count()).select_from(PositionRow)) or 0) == 1
        bet = s.scalars(select(LedgerRow).where(LedgerRow.kind == "BET")).one()
        # the simulated cash really pays the 2% fee on top of the stake
        assert bet.amount == pytest.approx(-d.stake * 1.02, rel=1e-9)


def test_the_default_configuration_is_untouched_by_the_new_model(
    factory: sessionmaker[Session],
) -> None:
    report = cycle(factory, T_CYCLE, DEFAULT)
    assert report.model_version is not None and report.model_version.startswith(
        "base-rate-shrinkage"
    )
    assert all(not d.model_version.startswith("recalibrated") for d in decisions(factory).values())


def test_the_forward_test_never_places_a_real_order_or_leaves_paper_mode(
    factory: sessionmaker[Session],
) -> None:
    assert FORWARD.execution_mode is ExecutionMode.PAPER and not FORWARD.enable_manifold_orders
    cycle(factory, T_CYCLE, FORWARD)
    with factory() as s:
        assert (s.scalar(select(func.count()).select_from(LedgerRow)) or 0) >= 1  # paper ledger
    bad = Settings(database_url="sqlite://", execution_mode=ExecutionMode.MANIFOLD_MANA)
    with pytest.raises(ExecutionModeNotAvailable):
        cycle(factory, T_CYCLE + timedelta(minutes=5), bad)


# ------------------------------------------------------------------ the pre-registered criterion


def _resolve(factory: sessionmaker[Session], market_id: str, outcome: str, at: Any) -> None:
    from ambre.infrastructure.orm import MarketRow

    with factory() as s:
        m = s.get(MarketRow, market_id)
        assert m is not None
        m.is_resolved, m.resolution, m.resolution_at = True, outcome, at
        s.commit()


def test_the_criterion_uses_the_first_in_window_decision_of_resolved_markets_only(
    factory: sessionmaker[Session],
) -> None:
    from ambre.infrastructure import repositories as repo

    cycle(factory, T_CYCLE, FORWARD)
    cycle(factory, T_CYCLE + timedelta(hours=2), FORWARD)  # a later decision, same market
    with factory() as s:
        obs, counts = repo.forward_observations(s, RECAL.min_age_days, RECAL.max_age_days)
    assert obs == [] and counts["markets_with_window_decision"] == 1  # nothing resolved yet

    _resolve(factory, "synthetic_in", "YES", T_CYCLE + timedelta(days=5))
    _resolve(factory, "synthetic_old", "NO", T_CYCLE + timedelta(days=5))  # never in the window
    with factory() as s:
        obs, counts = repo.forward_observations(s, RECAL.min_age_days, RECAL.max_age_days)
        first = s.scalars(
            select(DecisionRow)
            .where(DecisionRow.market_id == "synthetic_in")
            .order_by(DecisionRow.created_at)
        ).first()
        assert first is not None
    assert [o.market_id for o in obs] == ["synthetic_in"] and obs[0].outcome is True
    assert obs[0].estimated_probability == first.estimated_probability  # the FIRST decision
    assert counts == {"markets_with_window_decision": 1, "resolved_yes_no": 1, "pending": 0}


def test_a_market_resolved_before_the_decision_is_not_a_forecast(
    factory: sessionmaker[Session],
) -> None:
    from ambre.infrastructure import repositories as repo

    cycle(factory, T_CYCLE, FORWARD)
    _resolve(factory, "synthetic_in", "YES", T_CYCLE - timedelta(hours=1))
    with factory() as s:
        obs, _ = repo.forward_observations(s, RECAL.min_age_days, RECAL.max_age_days)
    assert obs == []


def test_decisions_of_another_model_never_enter_the_forward_criterion(
    factory: sessionmaker[Session],
) -> None:
    from ambre.infrastructure import repositories as repo

    cycle(factory, T_CYCLE, DEFAULT)  # the long-standing baseline decides here
    _resolve(factory, "synthetic_in", "YES", T_CYCLE + timedelta(days=5))
    with factory() as s:
        obs, counts = repo.forward_observations(s, RECAL.min_age_days, RECAL.max_age_days)
    assert obs == [] and counts["markets_with_window_decision"] == 0


def test_the_report_command_hides_the_verdict_below_the_threshold(
    factory: sessionmaker[Session],
) -> None:
    from ambre import cli

    cycle(factory, T_CYCLE, FORWARD)
    _resolve(factory, "synthetic_in", "YES", T_CYCLE + timedelta(days=5))
    out = cli.forward_report(factory, FORWARD)
    assert out is not None
    assert out["report"]["status"] == "INSUFFICIENT_SAMPLE" and out["report"]["n"] == 1
    assert out["report"]["ci95_clustered"] is None
    assert out["report"]["mean_brier_difference"] is None
    assert out["window_days"] == [2.0, 4.0]
    assert out["secondary"]["note"].startswith("descriptive")
    assert out["secondary"]["bets"] >= 0


def test_the_report_command_refuses_a_configuration_without_the_frozen_model(
    factory: sessionmaker[Session],
) -> None:
    from ambre import cli

    assert cli.forward_report(factory, DEFAULT) is None

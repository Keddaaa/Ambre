from datetime import UTC, datetime, timedelta

import pytest

from ambre.config.settings import PredictionConfig
from ambre.evaluation_engine.backtest import BacktestMarket, run_backtest

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CFG = PredictionConfig(shrinkage_market_weight=0.5)


def _m(
    market_id: str,
    decision_day: int,
    resolution_day: int | None,
    outcome: bool | None,
    q: float = 0.5,
) -> BacktestMarket:
    return BacktestMarket(
        market_id=market_id,
        decision_at=T0 + timedelta(days=decision_day),
        market_probability=q,
        resolution_at=None if resolution_day is None else T0 + timedelta(days=resolution_day),
        outcome=outcome,
    )


def test_empty_backtest_has_no_metrics_not_zero() -> None:
    result = run_backtest([], CFG)
    assert result.report.n == 0
    assert result.report.model_brier is None
    assert result.folds == []


def test_base_rate_uses_only_markets_resolved_before_the_decision() -> None:
    # "late" resolves YES at day 50, after b's decision (day 10): it must not be used.
    markets = [
        _m("early", 0, 5, False),
        _m("b", 10, 20, True),
        _m("late", 1, 50, True),
    ]
    result = run_backtest(markets, CFG)
    fold = next(f for f in result.folds if f.market_id == "b")
    # only "early" (NO) is known at day 10: rate = (0+1)/(1+2) = 1/3, w = 0.5, q = 0.5
    assert fold.n_train == 1
    assert fold.estimated_probability == pytest.approx(0.5 * 0.5 + 0.5 * (1 / 3))


def test_resolution_exactly_at_decision_time_is_not_known() -> None:
    markets = [_m("a", 0, 10, True), _m("b", 10, 20, False)]
    result = run_backtest(markets, CFG)
    fold = next(f for f in result.folds if f.market_id == "b")
    assert fold.n_train == 0


def test_unresolved_or_undated_markets_are_never_scored_or_used() -> None:
    markets = [
        _m("open", 0, None, None),
        _m("undated", 0, None, True),
        _m("ok", 5, 9, True),
    ]
    result = run_backtest(markets, CFG)
    assert [f.market_id for f in result.folds] == ["ok"]
    assert result.folds[0].n_train == 0


def test_decision_not_before_resolution_is_excluded() -> None:
    result = run_backtest([_m("a", 10, 10, True), _m("b", 11, 10, False)], CFG)
    assert result.report.n == 0


def test_report_compares_model_and_market_on_the_same_markets() -> None:
    markets = [_m(f"m{i}", i, 100 + i, i % 2 == 0, q=0.6) for i in range(4)]
    result = run_backtest(markets, CFG)
    assert result.report.n == 4
    assert result.report.market_brier == pytest.approx(
        sum((0.6 - (1.0 if i % 2 == 0 else 0.0)) ** 2 for i in range(4)) / 4
    )
    assert result.report.sufficient_sample is False

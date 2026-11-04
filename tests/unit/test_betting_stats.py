from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ambre.evaluation_engine.betting import (
    BetRecord,
    DecisionFunnel,
    betting_stats,
    wilson_interval,
)

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)
FUNNEL = DecisionFunnel(total=0, simulated=0, rejected=0, no_trade=0)


def bet(**overrides: Any) -> BetRecord:
    values: dict[str, Any] = {
        "market_id": "synthetic_m1",
        "question": "synthetic question",
        "side": "YES",
        "stake": 10.0,
        "shares": 20.0,
        "placed_at": T0,
        "settled_at": None,
        "payout": None,
        "resolution": None,
        "current_value": None,
        "estimated_win_probability": None,
    }
    return BetRecord(**(values | overrides))


def won(stake: float = 10.0, payout: float = 20.0, hours: int = 1, **kw: Any) -> BetRecord:
    settled_at = T0 + timedelta(hours=hours)
    return bet(stake=stake, payout=payout, resolution="YES", settled_at=settled_at, **kw)


def lost(stake: float = 10.0, hours: int = 1, **kw: Any) -> BetRecord:
    settled_at = T0 + timedelta(hours=hours)
    return bet(stake=stake, payout=0.0, resolution="NO", settled_at=settled_at, **kw)


def test_no_bets_gives_none_not_zero() -> None:
    s = betting_stats([], FUNNEL)
    assert s.bets == 0
    assert s.win_rate is None and s.win_rate_ci95 is None
    assert s.realized_pnl is None and s.unrealized_pnl is None and s.total_pnl is None
    assert s.roi is None and s.average_stake is None and s.median_stake is None
    assert s.best_bet is None and s.worst_bet is None
    assert s.pnl_curve == [] and s.bets_per_day == []


def test_win_rate_counts_only_decided_bets() -> None:
    bets = [
        won(),
        lost(),
        lost(),
        bet(),  # open
        bet(payout=10.0, resolution="CANCEL", settled_at=T0),  # refund
        bet(payout=10.0, resolution="MKT", settled_at=T0),  # break-even
    ]
    s = betting_stats(bets, FUNNEL)
    assert (s.bets, s.open, s.settled) == (6, 1, 5)
    assert (s.won, s.lost, s.refunded, s.pushed) == (1, 2, 1, 1)
    assert s.win_rate == pytest.approx(1 / 3)


def test_only_refunds_leave_win_rate_unknown() -> None:
    s = betting_stats([bet(payout=10.0, resolution="CANCEL", settled_at=T0)], FUNNEL)
    assert s.win_rate is None
    assert s.realized_pnl == 0.0
    assert s.roi is None


def test_partial_mkt_resolution_is_judged_on_money() -> None:
    bets = [
        bet(payout=14.0, resolution="MKT", settled_at=T0),
        bet(payout=6.0, resolution="MKT", settled_at=T0),
    ]
    s = betting_stats(bets, FUNNEL)
    assert (s.won, s.lost) == (1, 1)


def test_pnl_and_roi() -> None:
    bets = [
        won(stake=10.0, payout=30.0),
        lost(stake=5.0),
        bet(stake=8.0, current_value=6.0),
        bet(payout=4.0, stake=4.0, resolution="CANCEL", settled_at=T0),
    ]
    s = betting_stats(bets, FUNNEL)
    assert s.total_staked == pytest.approx(27.0)
    assert s.open_stake == pytest.approx(8.0)
    assert s.realized_pnl == pytest.approx(15.0)
    assert s.unrealized_pnl == pytest.approx(-2.0)
    assert s.total_pnl == pytest.approx(13.0)
    assert s.roi == pytest.approx(15.0 / 15.0)  # the refund risked nothing


def test_unknown_open_value_leaves_unrealized_and_total_unknown() -> None:
    s = betting_stats([won(), bet(current_value=None), bet(current_value=9.0)], FUNNEL)
    assert s.unrealized_pnl is None
    assert s.total_pnl is None
    assert s.realized_pnl == pytest.approx(10.0)


def test_no_open_bet_total_is_realized() -> None:
    s = betting_stats([won()], FUNNEL)
    assert s.unrealized_pnl is None
    assert s.total_pnl == pytest.approx(10.0)


def test_stake_summary() -> None:
    s = betting_stats([bet(stake=2.0), bet(stake=4.0), bet(stake=12.0)], FUNNEL)
    assert s.average_stake == pytest.approx(6.0)
    assert s.median_stake == pytest.approx(4.0)
    assert s.largest_stake == pytest.approx(12.0)
    assert s.smallest_stake == pytest.approx(2.0)


def test_win_and_loss_averages_and_profit_factor() -> None:
    bets = [won(payout=30.0), won(payout=20.0), lost(stake=10.0), lost(stake=20.0)]
    s = betting_stats(bets, FUNNEL)
    assert s.average_win == pytest.approx(15.0)
    assert s.average_loss == pytest.approx(-15.0)
    assert s.profit_factor == pytest.approx(1.0)


def test_profit_factor_is_unknown_without_a_loss() -> None:
    s = betting_stats([won()], FUNNEL)
    assert s.profit_factor is None
    assert s.average_loss is None


def test_profit_factor_is_zero_when_everything_lost() -> None:
    assert betting_stats([lost()], FUNNEL).profit_factor == 0.0


def test_best_and_worst_bet() -> None:
    bets = [won(payout=30.0, market_id="a"), lost(stake=7.0, market_id="b"), won(market_id="c")]
    s = betting_stats(bets, FUNNEL)
    assert s.best_bet is not None and s.best_bet.market_id == "a"
    assert s.worst_bet is not None and s.worst_bet.market_id == "b"
    assert s.worst_bet.pnl == pytest.approx(-7.0)


def test_streaks_follow_settlement_order_and_skip_refunds() -> None:
    bets = [
        lost(hours=5),
        won(hours=1),
        won(hours=2),
        bet(payout=10.0, resolution="CANCEL", settled_at=T0 + timedelta(hours=3)),
        won(hours=4),
        lost(hours=6),
    ]
    s = betting_stats(bets, FUNNEL)
    assert s.longest_win_streak == 3
    assert s.longest_loss_streak == 2
    assert s.current_streak == -2


def test_implied_win_rates_compare_to_the_actual_one() -> None:
    bets = [
        won(stake=2.0, shares=10.0, estimated_win_probability=0.3),  # entry price 0.2
        lost(stake=4.0, shares=10.0, estimated_win_probability=0.5),  # entry price 0.4
        bet(stake=9.0, shares=10.0, estimated_win_probability=0.95),  # open: not compared
    ]
    s = betting_stats(bets, FUNNEL)
    assert s.market_implied_win_rate == pytest.approx(0.3)
    assert s.model_implied_win_rate == pytest.approx(0.4)


def test_model_implied_win_rate_is_unknown_when_an_estimate_is_missing() -> None:
    bets = [won(estimated_win_probability=0.3), lost(estimated_win_probability=None)]
    assert betting_stats(bets, FUNNEL).model_implied_win_rate is None


def test_breakdown_by_side() -> None:
    s = betting_stats([won(side="YES"), lost(side="YES"), lost(side="NO", stake=3.0)], FUNNEL)
    groups = {g.label: g for g in s.by_side}
    assert groups["YES"].bets == 2 and groups["YES"].win_rate == pytest.approx(0.5)
    assert groups["NO"].won == 0 and groups["NO"].lost == 1
    assert groups["NO"].realized_pnl == pytest.approx(-3.0)


def test_breakdown_by_entry_price_keeps_every_bet_once() -> None:
    bets = [
        bet(stake=1.0, shares=20.0),  # 0.05
        bet(stake=1.0, shares=10.0),  # 0.10: lower bound is inclusive
        bet(stake=3.0, shares=10.0),  # 0.30
        bet(stake=6.0, shares=10.0),  # 0.60
        bet(stake=10.0, shares=10.0),  # 1.00: upper bound of the last bucket
    ]
    s = betting_stats(bets, FUNNEL)
    assert [g.bets for g in s.by_entry_price] == [1, 1, 1, 1, 1]


def test_group_without_decided_bet_has_no_win_rate_or_pnl() -> None:
    s = betting_stats([bet(side="NO")], FUNNEL)
    group = next(g for g in s.by_side if g.label == "NO")
    assert group.win_rate is None and group.realized_pnl is None


def test_pnl_curve_is_cumulative_in_settlement_order() -> None:
    bets = [lost(stake=5.0, hours=2), won(payout=30.0, hours=1)]
    s = betting_stats(bets, FUNNEL)
    assert [(p.index, p.cumulative_pnl) for p in s.pnl_curve] == [(1, 20.0), (2, 15.0)]


def test_bets_per_day_groups_on_utc_date() -> None:
    bets = [bet(stake=2.0), bet(stake=3.0), bet(placed_at=T0 + timedelta(days=2))]
    s = betting_stats(bets, FUNNEL)
    assert [(d.day, d.bets, d.staked) for d in s.bets_per_day] == [
        ("2026-11-01", 2, 5.0),
        ("2026-11-03", 1, 10.0),
    ]


def test_recent_settled_lists_latest_first_and_is_capped() -> None:
    bets = [lost(hours=h, market_id=f"m{h}") for h in range(1, 6)]
    s = betting_stats(bets, FUNNEL, recent_limit=2)
    assert [b.market_id for b in s.recent_settled] == ["m5", "m4"]


def test_sample_flag() -> None:
    s = betting_stats([lost()] * 2, FUNNEL, min_sample=2)
    assert s.sufficient_sample is True
    assert betting_stats([lost()], FUNNEL, min_sample=2).sufficient_sample is False


def test_funnel_is_passed_through() -> None:
    funnel = DecisionFunnel(total=5, simulated=1, rejected=1, no_trade=3)
    assert betting_stats([], funnel).decisions == funnel


def test_wilson_interval_known_values() -> None:
    low, high = wilson_interval(0, 10)
    assert low == pytest.approx(0.0, abs=1e-12)
    assert high == pytest.approx(0.2775, abs=1e-3)
    low, high = wilson_interval(5, 10)
    assert (low, high) == (pytest.approx(0.2366, abs=1e-3), pytest.approx(0.7634, abs=1e-3))


def test_wilson_interval_rejects_empty_sample() -> None:
    with pytest.raises(ValueError):
        wilson_interval(0, 0)

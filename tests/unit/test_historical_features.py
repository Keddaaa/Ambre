"""Historical features (ADR-022): only what was knowable at the instant."""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from inspect import signature

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ambre.evaluation_engine.history import BetEvent
from ambre.feature_engine.historical_features import (
    ACTIVITY_FEATURES,
    HISTORICAL_FEATURES,
    PATH_FEATURES,
    Availability,
    features_at,
    logit,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
AT = CREATED + timedelta(days=3)


def e(
    hours: float,
    before: float,
    after: float,
    amount: float | None = 10.0,
    bettor: str | None = "a",
    bet_id: str | None = None,
) -> BetEvent:
    return BetEvent(
        bet_id or f"b{hours}", CREATED + timedelta(hours=hours), before, after, amount, bettor, None
    )


EVENTS = (
    e(1, 0.50, 0.60, 10, "a"),
    e(10, 0.60, 0.70, 20, "b"),
    e(60, 0.70, 0.40, 30, "a"),
)


def test_only_past_bets_are_read() -> None:
    f = features_at(CREATED, EVENTS, CREATED + timedelta(hours=20))
    assert f["price"] == 0.70
    assert f["log_trades"] == pytest.approx(math.log1p(2))
    assert f["log_volume"] == pytest.approx(math.log1p(30))
    assert f["log_bettors"] == pytest.approx(math.log1p(2))


def test_a_bet_at_the_instant_is_known() -> None:
    assert features_at(CREATED, EVENTS, CREATED + timedelta(hours=60))["price"] == 0.40


def test_the_function_cannot_see_the_close_or_the_resolution() -> None:
    assert list(signature(features_at).parameters) == ["created_at", "events", "at"]


def test_final_values_are_declared_and_never_reach_a_model() -> None:
    final = {s.name for s in HISTORICAL_FEATURES if s.availability is Availability.FINAL_VALUE}
    assert {"seconds_to_close", "total_liquidity", "final_volume", "final_bettors"} <= final
    assert not final & set(PATH_FEATURES + ACTIVITY_FEATURES)
    assert not final & set(features_at(CREATED, EVENTS, AT))


def test_every_model_feature_is_specified_and_produced() -> None:
    known = {s.name for s in HISTORICAL_FEATURES}
    assert set(PATH_FEATURES + ACTIVITY_FEATURES) <= known
    assert set(PATH_FEATURES + ACTIVITY_FEATURES) <= set(features_at(CREATED, EVENTS, AT))


def test_price_moves_are_measured_in_logit() -> None:
    f = features_at(CREATED, EVENTS, AT)  # 72 h: the last 24 h hold the bet at 60 h
    assert f["logit_change_24h"] == pytest.approx(logit(0.40) - logit(0.70))
    f = features_at(CREATED, EVENTS, CREATED + timedelta(hours=61))
    assert f["logit_change_24h"] == pytest.approx(logit(0.40) - logit(0.70))
    assert f["logit_change_open"] == pytest.approx(logit(0.40) - logit(0.50))


def test_no_day_of_history_means_no_24h_change_not_zero() -> None:
    f = features_at(CREATED, EVENTS, CREATED + timedelta(hours=12))
    assert f["logit_change_24h"] is None


def test_recent_activity_only_counts_the_last_24_hours() -> None:
    f = features_at(CREATED, EVENTS, CREATED + timedelta(hours=61))
    assert f["log_trades_24h"] == pytest.approx(math.log1p(1))
    assert f["log_volume_24h"] == pytest.approx(math.log1p(30))
    assert f["log_hours_since_trade"] == pytest.approx(math.log1p(1))


def test_an_unknown_amount_makes_the_volume_unknown_not_smaller() -> None:
    f = features_at(CREATED, (*EVENTS[:2], e(30, 0.7, 0.7, None)), AT)
    assert f["log_volume"] is None


def test_an_unknown_bettor_makes_the_count_unknown_not_smaller() -> None:
    f = features_at(CREATED, (*EVENTS[:2], e(30, 0.7, 0.7, 5, None)), AT)
    assert f["log_bettors"] is None


def test_an_order_that_traded_nothing_is_not_a_trade() -> None:
    f = features_at(CREATED, (*EVENTS, e(62, 0.4, 0.4, 0.0)), CREATED + timedelta(hours=63))
    assert f["log_trades"] == pytest.approx(math.log1p(3))
    assert f["log_hours_since_trade"] == pytest.approx(math.log1p(3))


def test_before_the_first_bet_the_price_is_the_opening_one() -> None:
    f = features_at(CREATED, EVENTS, CREATED + timedelta(minutes=30))
    assert f["price"] == 0.50
    assert f["log_trades"] == 0.0  # counted: there really was no trade
    assert f["realized_volatility"] is None
    assert f["log_hours_since_trade"] is None


def test_without_any_bet_there_is_no_price_to_describe() -> None:
    assert features_at(CREATED, (), AT) == {}


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(ValueError):
        features_at(CREATED, EVENTS, datetime(2026, 1, 2))


@given(
    later=st.lists(
        st.tuples(
            st.floats(0.001, 1000),
            st.floats(0, 1),
            st.floats(0, 1),
            st.one_of(st.none(), st.floats(0, 1000)),
            st.one_of(st.none(), st.text(max_size=3)),
        ),
        max_size=10,
    ),
    cut=st.floats(0, 100),
)
def test_no_later_bet_can_change_anything(
    later: list[tuple[float, float, float, float | None, str | None]], cut: float
) -> None:
    """Later bets are placed after the first one: its ``probBefore`` is the opening price, a fact
    of the creation that no real later bet can replace."""
    at = CREATED + timedelta(hours=cut)
    start = max(at, EVENTS[0].at)
    future = [
        replace(e(0, b, a, m, u, f"later{i}"), at=start + timedelta(hours=dt))
        for i, (dt, b, a, m, u) in enumerate(later)
    ]
    assert features_at(CREATED, EVENTS, at) == features_at(CREATED, (*EVENTS, *future), at)

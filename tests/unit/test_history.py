from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ambre.evaluation_engine.history import (
    BetEvent,
    bet_events,
    history_from_bets,
    history_from_events,
    price_at,
)

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def bet(
    bet_id: str,
    at: datetime,
    before: float,
    after: float,
    *,
    cancelled: bool = False,
    redemption: bool = False,
) -> dict[str, Any]:
    return {
        "id": bet_id,
        "betId": bet_id,
        "contractId": "m1",
        "createdTime": ms(at),
        "probBefore": before,
        "probAfter": after,
        "isCancelled": cancelled,
        "isRedemption": redemption,
    }


BETS = [
    bet("b1", T0 + timedelta(hours=1), 0.50, 0.60),
    bet("b2", T0 + timedelta(hours=5), 0.60, 0.40),
    bet("b3", T0 + timedelta(days=2), 0.40, 0.70),
]


def test_price_is_the_last_known_price_at_or_before_the_instant() -> None:
    h = history_from_bets("m1", BETS, complete=True)
    assert price_at(h, T0 + timedelta(hours=1)) == 0.60  # a bet at the instant is known
    assert price_at(h, T0 + timedelta(hours=3)) == 0.60
    assert price_at(h, T0 + timedelta(hours=5)) == 0.40
    assert price_at(h, T0 + timedelta(days=30)) == 0.70


def test_before_the_first_bet_the_price_is_the_opening_probability() -> None:
    h = history_from_bets("m1", BETS, complete=True)
    assert h.initial_probability == 0.50
    assert price_at(h, T0 + timedelta(minutes=10)) == 0.50


def test_a_later_bet_never_leaks_into_an_earlier_price() -> None:
    h = history_from_bets("m1", BETS, complete=True)
    at = T0 + timedelta(hours=4, minutes=59)
    assert price_at(h, at) == 0.60  # b2 (0.40) happened one minute later


def test_input_order_does_not_matter() -> None:
    forward = history_from_bets("m1", BETS, complete=True)
    backward = history_from_bets("m1", list(reversed(BETS)), complete=True)
    assert forward == backward


def test_unfilled_cancelled_orders_and_redemptions_do_not_move_the_price() -> None:
    noisy = [
        *BETS,
        bet("c1", T0 + timedelta(days=3), 0.70, 0.70, cancelled=True),
        bet("r1", T0 + timedelta(days=3, hours=1), 0.70, 0.70, redemption=True),
    ]
    h = history_from_bets("m1", noisy, complete=True)
    assert price_at(h, T0 + timedelta(days=4)) == 0.70
    assert len(h.points) == 3


def test_a_cancelled_order_that_was_partly_filled_did_move_the_price() -> None:
    """Seen on chlzpuPE2n: a limit order bought 0.16 -> 0.98, then its remainder was cancelled."""
    h = history_from_bets(
        "m1",
        [*BETS, bet("c1", T0 + timedelta(days=3), 0.70, 0.98, cancelled=True)],
        complete=True,
    )
    assert price_at(h, T0 + timedelta(days=4)) == 0.98


def event(raw: dict[str, Any]) -> BetEvent:
    events, dropped = bet_events([raw])
    assert dropped == 0 and len(events) == 1
    return events[0]


def test_a_market_order_counts_its_whole_amount() -> None:
    raw = bet("b", T0, 0.5, 0.6) | {"amount": 10.0, "userId": "u1", "isApi": True}
    e = event(raw)
    assert (e.amount, e.is_api, e.prob_before, e.prob_after) == (10.0, True, 0.5, 0.6)
    assert e.bettor is not None and "u1" not in e.bettor


def test_only_the_fills_of_the_bets_own_instant_count_as_its_volume() -> None:
    """``amount`` is a final value: a resting order keeps filling after it was placed."""
    raw = bet("b", T0, 0.5, 0.5) | {
        "amount": 30.0,
        "limitProb": 0.5,
        "fills": [
            {"amount": 10.0, "timestamp": ms(T0)},
            {"amount": 20.0, "timestamp": ms(T0 + timedelta(days=2))},
        ],
    }
    assert event(raw).amount == 10.0


def test_a_sale_counts_as_traded_volume() -> None:
    raw = bet("b", T0, 0.5, 0.4) | {
        "amount": -8.0,
        "fills": [{"amount": -8.0, "timestamp": ms(T0)}],
    }
    assert event(raw).amount == 8.0


def test_a_limit_order_without_fill_data_has_an_unknown_amount_not_zero() -> None:
    raw = bet("b", T0, 0.5, 0.5) | {"amount": 30.0, "limitProb": 0.5}
    assert event(raw).amount is None


def test_a_resting_order_with_an_empty_fill_list_traded_nothing() -> None:
    raw = bet("b", T0, 0.5, 0.5) | {"amount": 30.0, "limitProb": 0.5, "fills": []}
    assert event(raw).amount == 0.0


def test_events_follow_the_same_filter_as_the_prices() -> None:
    raws = [
        *BETS,
        bet("c0", T0 + timedelta(days=3), 0.70, 0.70, cancelled=True),
        bet("c1", T0 + timedelta(days=3), 0.70, 0.98, cancelled=True),
        bet("r1", T0 + timedelta(days=4), 0.98, 0.98, redemption=True),
        "not a bet",
    ]
    events, dropped = bet_events(raws)
    h = history_from_bets("m1", raws, complete=True)
    assert dropped == 1 == h.dropped
    assert [e.bet_id for e in events] == [p.bet_id for p in h.points]
    assert history_from_events("m1", events, complete=True, dropped=dropped) == h


def test_the_same_bettor_keeps_the_same_anonymous_key() -> None:
    a = event(bet("a", T0, 0.5, 0.6) | {"userId": "u1"})
    b = event(bet("b", T0, 0.5, 0.6) | {"userId": "u1"})
    c = event(bet("c", T0, 0.5, 0.6) | {"userId": "u2"})
    assert a.bettor == b.bettor != c.bettor
    assert event(bet("d", T0, 0.5, 0.6)).bettor is None


def test_an_incomplete_history_gives_no_price_rather_than_a_guess() -> None:
    h = history_from_bets("m1", BETS, complete=False)
    assert price_at(h, T0 + timedelta(hours=3)) is None


def test_a_market_without_bets_has_no_price() -> None:
    h = history_from_bets("m1", [], complete=True)
    assert h.initial_probability is None and price_at(h, T0) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "x", "createdTime": "yesterday", "probBefore": 0.5, "probAfter": 0.6},
        {"id": "x", "createdTime": 1, "probBefore": 1.5, "probAfter": 0.6},
        {"id": "x", "createdTime": 1, "probBefore": 0.5, "probAfter": float("nan")},
        {"id": "x", "createdTime": 1, "probBefore": 0.5},
        {"createdTime": 1, "probBefore": 0.5, "probAfter": 0.6},
        "not a bet",
    ],
)
def test_malformed_bets_are_dropped_never_turned_into_prices(broken: Any) -> None:
    h = history_from_bets("m1", [*BETS, broken], complete=True)
    assert len(h.points) == 3 and h.dropped == 1


def test_naive_instants_are_refused() -> None:
    h = history_from_bets("m1", BETS, complete=True)
    with pytest.raises(ValueError):
        price_at(h, datetime(2026, 8, 2))

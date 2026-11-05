from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ambre.evaluation_engine.history import history_from_bets, price_at

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


def test_cancelled_and_redemption_bets_do_not_move_the_price() -> None:
    noisy = [
        *BETS,
        bet("c1", T0 + timedelta(hours=6), 0.40, 0.99, cancelled=True),
        bet("r1", T0 + timedelta(hours=7), 0.40, 0.40, redemption=True),
    ]
    h = history_from_bets("m1", noisy, complete=True)
    assert price_at(h, T0 + timedelta(hours=8)) == 0.40
    assert len(h.points) == 3


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

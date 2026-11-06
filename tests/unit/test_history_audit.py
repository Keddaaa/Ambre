"""Audit of the bet-level history (ADR-022), on synthetic markets."""

from datetime import UTC, datetime, timedelta

import pytest

from ambre.evaluation_engine.history import BetEvent, BetLevelMarket, PricePoint
from ambre.evaluation_engine.history_audit import LiveSnapshot, audit_history

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def ev(hours: float, before: float, after: float, name: str | None = None) -> BetEvent:
    return BetEvent(name or f"b{hours}", T0 + timedelta(hours=hours), before, after, 5.0, "u", None)


def market(
    market_id: str = "m",
    events: tuple[BetEvent, ...] = (ev(1, 0.5, 0.6), ev(2, 0.6, 0.7)),
    *,
    close_days: float = 10,
    resolve_days: float = 10,
    final: float | None = 0.7,
    complete: bool = True,
) -> BetLevelMarket:
    return BetLevelMarket(
        market_id=market_id,
        question=f"question {market_id}",
        creator_id="c",
        created_at=T0,
        close_at=T0 + timedelta(days=close_days),
        resolution_at=T0 + timedelta(days=resolve_days),
        outcome=True,
        complete=complete,
        bet_count=len(events),
        dropped=0,
        fetched_at=T0 + timedelta(days=20),
        events=events,
        final_probability=final,
    )


def test_a_clean_market_raises_nothing() -> None:
    a = audit_history([market()], {}, [])
    assert a.markets == 1 and a.complete == 1
    assert a.chain_breaks == 0 and a.final_price_mismatches == 0
    assert a.events_before_creation == 0 and a.events_after_resolution == 0


def test_a_missing_price_move_shows_as_a_chain_break() -> None:
    a = audit_history([market(events=(ev(1, 0.5, 0.6), ev(2, 0.9, 0.95)), final=0.95)], {}, [])
    assert a.chain_breaks == 1 and a.markets_with_chain_breaks == 1


def test_a_last_price_far_from_the_final_one_is_counted() -> None:
    a = audit_history([market(final=0.99)], {}, [])
    assert a.final_price_mismatches == 1


def test_a_close_rewritten_to_the_resolution_is_measured() -> None:
    a = audit_history(
        [market("a"), market("b", close_days=5, resolve_days=10)],
        {},
        [],
    )
    assert a.close_equals_resolution == 1
    assert a.close_equals_resolution_share == pytest.approx(0.5)
    assert a.close_after_resolution == 0


def test_timing_anomalies_are_counted() -> None:
    a = audit_history(
        [market(events=(ev(-1, 0.5, 0.6), ev(24 * 11, 0.6, 0.7)))],
        {},
        [],
    )
    assert a.events_before_creation == 1 and a.events_after_resolution == 1


def test_a_live_snapshot_checks_the_rebuilt_price_independently() -> None:
    snaps = [
        LiveSnapshot("m", T0 + timedelta(hours=1, minutes=30), 0.60),
        LiveSnapshot("m", T0 + timedelta(hours=3), 0.50),
        LiveSnapshot("unknown", T0, 0.5),
    ]
    a = audit_history([market()], {}, snaps)
    assert a.snapshots_compared == 2 and a.snapshot_mismatches == 1


def test_the_former_points_are_compared_at_each_horizon() -> None:
    old = {"m": (0.5, (PricePoint(T0 + timedelta(hours=1), 0.6, "b1"),))}
    a = audit_history([market(events=(ev(1, 0.5, 0.6), ev(30, 0.6, 0.9)))], old, [])
    one_day, three_days = a.old_vs_corrected[0], a.old_vs_corrected[1]
    assert (one_day.horizon_days, one_day.n, one_day.moved_over_1pt) == (1, 1, 0)
    assert (three_days.horizon_days, three_days.moved_over_1pt, three_days.moved_over_5pt) == (
        3,
        1,
        1,
    )
    assert three_days.mean_abs_difference == pytest.approx(0.3)


def test_unknown_amounts_and_bettors_are_counted_not_hidden() -> None:
    e = BetEvent("x", T0 + timedelta(hours=1), 0.5, 0.6, None, None, None)
    a = audit_history([market(events=(e,), final=0.6)], {}, [])
    assert a.events_without_amount == 1 and a.events_without_bettor == 1


def test_an_incomplete_read_is_counted_and_left_out_of_the_checks() -> None:
    a = audit_history([market(complete=False, final=0.1)], {}, [])
    assert a.complete == 0 and a.final_price_mismatches == 0


def test_censoring_is_estimated_from_the_oldest_cohorts() -> None:
    long_lived = [market(f"m{i}", resolve_days=100 + i, close_days=100 + i) for i in range(4)]
    a = audit_history(long_lived, {}, [])
    assert a.lifetime_days_quantiles["p50"] == pytest.approx(101.5)
    assert a.expected_unresolved_at_age_days["60"] == 1.0
    assert a.expected_unresolved_at_age_days["215"] == 0.0

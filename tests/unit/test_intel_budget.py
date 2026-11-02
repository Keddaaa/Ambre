"""Budget Controller: strict rolling quotas, priority reserve, pauses (pure)."""

from datetime import UTC, datetime, timedelta

from ambre.intelligence.budget import Priority, backoff_seconds, decide
from ambre.intelligence.models import RateLimits

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def at(seconds_ago: float) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


def test_no_internal_budget_means_no_calls() -> None:
    d = decide(RateLimits(), [], NOW, priority=Priority.PRIMARY, reserve_fraction=0.2)
    assert not d.allowed
    assert "no internal budget" in d.reason


def test_within_quota_is_allowed_and_reports_remaining() -> None:
    d = decide(
        RateLimits(per_minute=5, per_day=100),
        [at(10), at(20)],
        NOW,
        priority=Priority.PRIMARY,
        reserve_fraction=0.2,
    )
    assert d.allowed
    assert d.remaining == {"minute": 3, "hour": None, "day": 98}


def test_a_full_window_blocks_and_says_when_to_retry() -> None:
    usage = [at(50), at(40), at(30)]
    d = decide(RateLimits(per_minute=3), usage, NOW, priority=Priority.PRIMARY, reserve_fraction=0)
    assert not d.allowed
    assert d.wait_seconds is not None and 9 < d.wait_seconds <= 10  # oldest leaves in 10 s
    assert "minute" in d.reason


def test_calls_outside_the_rolling_window_do_not_count() -> None:
    d = decide(
        RateLimits(per_minute=1), [at(61)], NOW, priority=Priority.PRIMARY, reserve_fraction=0
    )
    assert d.allowed


def test_secondary_work_cannot_use_the_priority_reserve() -> None:
    limits = RateLimits(per_day=10)
    usage = [at(100 * i) for i in range(1, 9)]  # 8 used, 2 left; reserve 20% = 2 calls
    secondary = decide(limits, usage, NOW, priority=Priority.SECONDARY, reserve_fraction=0.2)
    primary = decide(limits, usage, NOW, priority=Priority.PRIMARY, reserve_fraction=0.2)
    assert not secondary.allowed and "reserve" in secondary.reason
    assert primary.allowed


def test_min_interval_is_enforced() -> None:
    limits = RateLimits(min_interval_seconds=10.0, per_day=1000)
    d = decide(limits, [at(4)], NOW, priority=Priority.PRIMARY, reserve_fraction=0)
    assert not d.allowed
    assert d.wait_seconds is not None and 5.9 < d.wait_seconds <= 6.0
    assert decide(limits, [at(11)], NOW, priority=Priority.PRIMARY, reserve_fraction=0).allowed


def test_pause_blocks_until_its_end() -> None:
    d = decide(
        RateLimits(per_day=100),
        [],
        NOW,
        priority=Priority.PRIMARY,
        reserve_fraction=0,
        paused_until=NOW + timedelta(seconds=90),
    )
    assert not d.allowed
    assert d.wait_seconds == 90.0
    ended = decide(
        RateLimits(per_day=100),
        [],
        NOW,
        priority=Priority.PRIMARY,
        reserve_fraction=0,
        paused_until=NOW - timedelta(seconds=1),
    )
    assert ended.allowed


def test_tightest_window_wins() -> None:
    limits = RateLimits(per_minute=100, per_hour=2, per_day=1000)
    d = decide(limits, [at(100), at(200)], NOW, priority=Priority.PRIMARY, reserve_fraction=0)
    assert not d.allowed and "hour" in d.reason


def test_backoff_grows_exponentially_and_is_capped() -> None:
    assert backoff_seconds(0, base=30, cap=3600) == 30
    assert backoff_seconds(1, base=30, cap=3600) == 60
    assert backoff_seconds(3, base=30, cap=3600) == 240
    assert backoff_seconds(20, base=30, cap=3600) == 3600


def test_provider_retry_after_wins_over_a_shorter_backoff() -> None:
    assert backoff_seconds(0, base=30, cap=3600, retry_after=300.0) == 300.0
    assert backoff_seconds(0, base=30, cap=3600, retry_after=1.0) == 30

from datetime import timedelta

import pytest
from builders import NOW, make_market, make_snapshot

from ambre.config.settings import EligibilityConfig
from ambre.value_engine.eligibility import check_eligibility

CFG = EligibilityConfig()


def test_eligible_market_has_no_reason():
    assert check_eligibility(make_market(), make_snapshot(), NOW, CFG) == []


@pytest.mark.parametrize(
    ("market_kw", "snap_kw", "fragment"),
    [
        ({"is_resolved": True, "resolution": "YES"}, {}, "resolved"),
        ({"close_at": NOW - timedelta(seconds=1)}, {}, "closed"),
        ({"close_at": NOW}, {}, "closed"),
        ({"close_at": NOW + timedelta(seconds=3599)}, {}, "closes in"),
        ({"close_at": None}, {}, "close time unknown"),
        ({"token": "CASH"}, {}, "CASH"),
        ({}, {"total_liquidity": None}, "liquidity unknown"),
        ({}, {"total_liquidity": 99.9}, "liquidity 99.9"),
        ({}, {"unique_bettor_count": None}, "bettor count unknown"),
        ({}, {"unique_bettor_count": 4}, "bettors 4"),
    ],
)
def test_eligibility_reasons(market_kw, snap_kw, fragment):
    reasons = check_eligibility(make_market(**market_kw), make_snapshot(**snap_kw), NOW, CFG)
    assert len(reasons) == 1
    assert fragment in reasons[0]


def test_eligibility_boundaries_are_inclusive():
    market = make_market(close_at=NOW + timedelta(seconds=3600))
    snap = make_snapshot(total_liquidity=100.0, unique_bettor_count=5)
    assert check_eligibility(market, snap, NOW, CFG) == []


def test_eligibility_accumulates_reasons():
    market = make_market(is_resolved=True, close_at=None, token="CASH")
    snap = make_snapshot(total_liquidity=None, unique_bettor_count=None)
    assert len(check_eligibility(market, snap, NOW, CFG)) == 5

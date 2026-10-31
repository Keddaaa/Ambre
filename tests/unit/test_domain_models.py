from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from ambre.domain.models import (
    BinaryMarket,
    Estimate,
    MarketSnapshot,
    Proposal,
    RiskVerdict,
    Side,
    SurvivalAssessment,
    SurvivalState,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def snapshot_kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        market_id="m",
        probability=0.5,
        pool_yes=100,
        pool_no=100,
        p=0.5,
        total_liquidity=None,
        volume=None,
        volume_24h=None,
        unique_bettor_count=None,
        observed_at=NOW,
        event_at=None,
        source_id="manifold:/v0/markets",
    )
    base.update(over)
    return base


def market_kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="a",
        question="q?",
        slug="q",
        url="https://manifold.markets/x/q",
        creator_id="c",
        creator_username="u",
        mechanism="cpmm-1",
        created_at=NOW,
        close_at=None,
        is_resolved=False,
        resolution=None,
        resolution_probability=None,
        resolution_at=None,
        token=None,
    )
    base.update(over)
    return base


def test_probability_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Estimate(probability=1.2, uncertainty=0.1, model_version="x", inputs={})


def test_estimate_uncertainty_bounds():
    with pytest.raises(ValidationError):
        Estimate(probability=0.5, uncertainty=0.6, model_version="x", inputs={})
    with pytest.raises(ValidationError):
        Estimate(probability=0.5, uncertainty=-0.1, model_version="x", inputs={})


def test_snapshot_keeps_missing_fields_as_none():
    s = MarketSnapshot(**snapshot_kwargs())
    assert s.unique_bettor_count is None and s.event_at is None
    assert s.total_liquidity is None and s.volume is None


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        MarketSnapshot(**snapshot_kwargs(observed_at=datetime(2026, 1, 1)))
    with pytest.raises(ValidationError):
        MarketSnapshot(**snapshot_kwargs(event_at=datetime(2026, 1, 1)))
    with pytest.raises(ValidationError):
        BinaryMarket(**market_kwargs(created_at=datetime(2026, 1, 1)))


@pytest.mark.parametrize(
    "over",
    [{"pool_yes": 0}, {"pool_no": -1}, {"p": 0}, {"p": 1}, {"probability": -0.1}],
)
def test_snapshot_invalid_values_rejected(over):
    with pytest.raises(ValidationError):
        MarketSnapshot(**snapshot_kwargs(**over))


def test_models_are_frozen():
    s = MarketSnapshot(**snapshot_kwargs())
    with pytest.raises(ValidationError):
        s.probability = 0.9  # type: ignore[misc]


def test_binary_market_group_slugs_default_not_shared():
    a = BinaryMarket(**market_kwargs())
    b = BinaryMarket(**market_kwargs(id="b"))
    assert a.group_slugs == [] and b.group_slugs == []
    assert a.group_slugs is not b.group_slugs


def test_proposal_and_verdict_constraints():
    est = Estimate(probability=0.6, uncertainty=0.05, model_version="v", inputs={"a": 1, "b": None})
    p = Proposal(
        market_id="m",
        side=Side.NO,
        stake=5.0,
        conservative_probability=0.4,
        estimate=est,
        reasons=["r"],
        invalidation=["i"],
    )
    assert p.side == "NO"
    with pytest.raises(ValidationError):
        Proposal(
            market_id="m",
            side=Side.YES,
            stake=-1,
            conservative_probability=0.4,
            estimate=est,
            reasons=[],
            invalidation=[],
        )
    with pytest.raises(ValidationError):
        RiskVerdict(approved=True, stake=-0.1, reasons=[])


def test_survival_assessment_multiplier_bounds():
    ok = SurvivalAssessment(
        state=SurvivalState.BALANCED,
        stake_multiplier=1.0,
        allow_new_positions=True,
        reasons=[],
    )
    assert ok.state == "BALANCED"
    with pytest.raises(ValidationError):
        SurvivalAssessment(
            state=SurvivalState.HALTED,
            stake_multiplier=1.5,
            allow_new_positions=False,
            reasons=[],
        )

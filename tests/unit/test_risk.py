import pytest
from builders import balanced, make_portfolio, make_proposal, make_snapshot

from ambre.config.settings import RiskConfig
from ambre.domain.models import Side, SurvivalAssessment, SurvivalState
from ambre.risk_engine.risk import RiskEngine

ENGINE = RiskEngine(RiskConfig())
DEEP = make_snapshot(pool_yes=100_000.0, pool_no=100_000.0)


def test_risk_caps_stake_to_2pct():
    verdict, fv = ENGINE.evaluate(make_proposal(stake=40), DEEP, make_portfolio(), balanced())
    assert verdict.approved
    assert verdict.stake == pytest.approx(20)
    assert fv is not None
    assert fv.fill.amount == pytest.approx(20)
    assert fv.ev_per_mana >= 0.03


def test_risk_rejects_when_impact_kills_edge():
    # Raw edge 0.55 - 0.5 = 0.05, but stake 20 into a 100/100 pool buys ~36.67 shares.
    snap = make_snapshot(pool_yes=100.0, pool_no=100.0, p=0.5)
    verdict, fv = ENGINE.evaluate(
        make_proposal(stake=20, p_cons=0.55), snap, make_portfolio(), balanced()
    )
    assert not verdict.approved
    assert verdict.stake == 0
    assert fv is not None
    assert fv.fill.shares == pytest.approx(36.67, abs=0.01)
    assert fv.ev_per_mana == pytest.approx(0.0083, abs=1e-4)
    assert any(r.startswith("edge after impact 0.0083 < 0.03") for r in verdict.reasons)


def test_risk_uses_no_side_probability():
    # P(YES)=0.42 → P(NO)=0.58 ; buying NO at 0.5 in a deep pool is a good bet.
    verdict, fv = ENGINE.evaluate(
        make_proposal(stake=10, side=Side.NO, p_cons=0.42), DEEP, make_portfolio(), balanced()
    )
    assert verdict.approved
    assert fv is not None
    assert fv.expected_payout == pytest.approx(0.58 * fv.fill.shares)


def test_risk_rejects_existing_position():
    portfolio = make_portfolio(open_market_ids=frozenset({"synthetic_m1"}))
    verdict, fv = ENGINE.evaluate(make_proposal(), DEEP, portfolio, balanced())
    assert not verdict.approved
    assert verdict.stake == 0
    assert fv is None
    assert any("open position" in r for r in verdict.reasons)


def test_risk_rejects_below_min_stake():
    verdict, fv = ENGINE.evaluate(make_proposal(stake=0.5), DEEP, make_portfolio(), balanced())
    assert not verdict.approved
    assert fv is None
    assert any("below min stake" in r for r in verdict.reasons)


def test_risk_respects_total_exposure():
    portfolio = make_portfolio(open_exposure=195.0)
    verdict, _ = ENGINE.evaluate(make_proposal(stake=40), DEEP, portfolio, balanced())
    assert verdict.approved
    assert verdict.stake == pytest.approx(5)


def test_risk_limits_to_cash():
    verdict, _ = ENGINE.evaluate(
        make_proposal(stake=40), DEEP, make_portfolio(cash=3.0), balanced()
    )
    assert verdict.approved
    assert verdict.stake == pytest.approx(3)


def test_risk_applies_survival_multiplier():
    defensive = SurvivalAssessment(
        state=SurvivalState.DEFENSIVE, stake_multiplier=0.5, allow_new_positions=True, reasons=[]
    )
    verdict, _ = ENGINE.evaluate(make_proposal(stake=10), DEEP, make_portfolio(), defensive)
    assert verdict.approved
    assert verdict.stake == pytest.approx(5)


def test_risk_rejects_when_survival_forbids_or_exposure_unknown():
    blocked = SurvivalAssessment(
        state=SurvivalState.OBSERVING, stake_multiplier=0.0, allow_new_positions=False, reasons=[]
    )
    verdict, fv = ENGINE.evaluate(
        make_proposal(), DEEP, make_portfolio(exposure_known=False), blocked
    )
    assert not verdict.approved
    assert fv is None
    assert len(verdict.reasons) == 2
    assert "OBSERVING" in verdict.reasons[0]
    assert "exposure unknown" in verdict.reasons[1]


def test_risk_turns_cpmm_error_into_rejection():
    # A tiny pool with an extreme p underflows the surviving pool.
    snap = make_snapshot(pool_yes=1e-3, pool_no=1e-3, p=0.01, probability=0.01)
    portfolio = make_portfolio(cash=1e9, total_value=1e9)
    verdict, fv = ENGINE.evaluate(make_proposal(stake=1e6), snap, portfolio, balanced())
    assert not verdict.approved
    assert fv is None
    assert any("fill simulation failed" in r for r in verdict.reasons)


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize(
    "kw",
    [
        {"total_value": NAN},
        {"total_value": INF},
        {"total_value": 0.0},
        {"total_value": -10.0},
        {"cash": NAN},
        {"cash": INF},
        {"open_exposure": NAN},
        {"open_exposure": -INF},
    ],
)
def test_risk_rejects_non_finite_portfolio(kw):
    verdict, fv = ENGINE.evaluate(make_proposal(stake=40), DEEP, make_portfolio(**kw), balanced())
    assert not verdict.approved
    assert verdict.stake == 0
    assert fv is None
    assert any("invalid portfolio valuation" in r for r in verdict.reasons)


@pytest.mark.parametrize("stake", [NAN, INF])
def test_risk_rejects_non_finite_proposal_stake(stake):
    proposal = make_proposal().model_copy(update={"stake": stake})
    verdict, fv = ENGINE.evaluate(proposal, DEEP, make_portfolio(), balanced())
    assert not verdict.approved
    assert verdict.stake == 0
    assert fv is None
    assert any("non-finite proposal stake" in r for r in verdict.reasons)


FEE_ENGINE = RiskEngine(RiskConfig(fee_rate=0.05))


def test_risk_caps_fee_inclusive_cost_to_cash():
    verdict, fv = FEE_ENGINE.evaluate(
        make_proposal(stake=40), DEEP, make_portfolio(cash=10.5), balanced()
    )
    assert verdict.approved
    assert verdict.stake == pytest.approx(10.0)
    assert fv is not None
    assert fv.cost == pytest.approx(10.5)


def test_risk_caps_fee_inclusive_cost_to_exposure_room():
    portfolio = make_portfolio(open_exposure=189.5)  # room 10.5
    verdict, fv = FEE_ENGINE.evaluate(make_proposal(stake=40), DEEP, portfolio, balanced())
    assert verdict.approved
    assert verdict.stake == pytest.approx(10.0)
    assert fv is not None
    assert portfolio.open_exposure + fv.cost <= 200 + 1e-9


def test_capacity_is_cash_and_exposure_room_net_of_fees():
    engine = RiskEngine(RiskConfig(max_total_exposure_fraction=0.2, fee_rate=0.0))
    assert engine.capacity(make_portfolio()) == 200.0
    assert engine.capacity(make_portfolio(open_exposure=150.0)) == 50.0
    assert engine.capacity(make_portfolio(cash=30.0, open_exposure=150.0)) == 30.0
    assert engine.capacity(make_portfolio(open_exposure=250.0)) == 0.0
    fees = RiskEngine(RiskConfig(max_total_exposure_fraction=0.2, fee_rate=0.25))
    assert fees.capacity(make_portfolio()) == 160.0
    assert engine.capacity(make_portfolio(total_value=float("nan"))) == 0.0

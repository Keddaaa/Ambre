import math

from builders import make_proposal, make_snapshot
from hypothesis import event, given, settings
from hypothesis import strategies as st

from ambre.config.settings import RiskConfig, SurvivalConfig
from ambre.domain.models import Side, SurvivalAssessment, SurvivalState
from ambre.risk_engine.risk import PortfolioView, RiskEngine
from ambre.survival_engine.survival import SurvivalInputs, assess_survival

CFG = RiskConfig()
ENGINE = RiskEngine(CFG)
REACHABLE = {
    SurvivalState.BALANCED,
    SurvivalState.DEFENSIVE,
    SurvivalState.OBSERVING,
    SurvivalState.CRITICAL,
    SurvivalState.HALTED,
}

proposals = st.builds(
    make_proposal,
    stake=st.floats(min_value=0, max_value=1e5),
    side=st.sampled_from([Side.YES, Side.NO]),
    p_cons=st.floats(min_value=0.001, max_value=0.999),
    market_id=st.sampled_from(["synthetic_m1", "synthetic_m2"]),
)
snapshots = st.builds(
    make_snapshot,
    pool_yes=st.floats(min_value=1, max_value=1e6),
    pool_no=st.floats(min_value=1, max_value=1e6),
    p=st.floats(min_value=0.01, max_value=0.99),
)
portfolios = st.builds(
    PortfolioView,
    cash=st.floats(min_value=-100, max_value=1e5),
    total_value=st.floats(min_value=0, max_value=1e5),
    open_exposure=st.floats(min_value=0, max_value=1e5),
    open_market_ids=st.frozensets(st.sampled_from(["synthetic_m1", "synthetic_m3"])),
    exposure_known=st.booleans(),
)
survivals = st.builds(
    SurvivalAssessment,
    state=st.sampled_from(sorted(REACHABLE)),
    stake_multiplier=st.floats(min_value=0, max_value=1),
    allow_new_positions=st.booleans(),
    reasons=st.just([]),
)


def _check_invariants(proposal, snap, portfolio, survival):
    verdict, fv = ENGINE.evaluate(proposal, snap, portfolio, survival)
    event("approved" if verdict.approved else "rejected")
    assert verdict.stake >= 0
    if not verdict.approved:
        assert verdict.stake == 0
    if not survival.allow_new_positions:
        assert not verdict.approved
    if verdict.approved:
        v = portfolio.total_value
        assert verdict.stake >= CFG.min_stake
        assert verdict.stake <= proposal.stake * survival.stake_multiplier + 1e-9
        assert verdict.stake <= CFG.max_position_fraction * v
        assert verdict.stake <= portfolio.cash
        assert portfolio.open_exposure + verdict.stake <= CFG.max_total_exposure_fraction * v + 1e-9
        assert fv is not None
        assert fv.fill.amount == verdict.stake
        assert fv.ev_per_mana >= CFG.minimum_edge
        assert proposal.market_id not in portfolio.open_market_ids
        assert portfolio.exposure_known


@given(proposals, snapshots, portfolios, survivals)
def test_risk_verdict_invariants(proposal, snap, portfolio, survival):
    _check_invariants(proposal, snap, portfolio, survival)


# Same invariants on inputs that pass the gates, so that approvals are frequent.
sizable_proposals = st.builds(
    make_proposal,
    stake=st.floats(min_value=1, max_value=1e4),
    side=st.sampled_from([Side.YES, Side.NO]),
    p_cons=st.floats(min_value=0.001, max_value=0.999),
)
deep_snapshots = st.builds(
    make_snapshot,
    pool_yes=st.floats(min_value=1e3, max_value=1e6),
    pool_no=st.floats(min_value=1e3, max_value=1e6),
    p=st.floats(min_value=0.2, max_value=0.8),
)
gate_passing_portfolios = st.floats(min_value=100, max_value=1e5).flatmap(
    lambda v: st.builds(
        PortfolioView,
        cash=st.floats(min_value=0, max_value=v),
        total_value=st.just(v),
        open_exposure=st.floats(min_value=0, max_value=0.22 * v),
        open_market_ids=st.just(frozenset({"synthetic_m3"})),
        exposure_known=st.just(True),
    )
)
open_survivals = st.builds(
    SurvivalAssessment,
    state=st.sampled_from([SurvivalState.BALANCED, SurvivalState.DEFENSIVE]),
    stake_multiplier=st.floats(min_value=0.1, max_value=1),
    allow_new_positions=st.just(True),
    reasons=st.just([]),
)


@settings(max_examples=500)
@given(sizable_proposals, deep_snapshots, gate_passing_portfolios, open_survivals)
def test_risk_verdict_invariants_past_gates(proposal, snap, portfolio, survival):
    _check_invariants(proposal, snap, portfolio, survival)


values = st.floats(min_value=0, max_value=1e5)


@given(
    total=values,
    peak=st.floats(min_value=1e-3, max_value=1e5),
    initial=st.floats(min_value=1e-3, max_value=1e5),
    age=st.none() | st.floats(min_value=0, max_value=1e5),
    known=st.booleans(),
    halted=st.booleans(),
)
def test_survival_state_is_reachable(total, peak, initial, age, known, halted):
    a = assess_survival(
        SurvivalInputs(total, peak, initial, age, known, halted), SurvivalConfig(), RiskConfig()
    )
    assert a.state in REACHABLE
    if a.state in {SurvivalState.HALTED, SurvivalState.OBSERVING, SurvivalState.CRITICAL}:
        assert not a.allow_new_positions
        assert a.stake_multiplier == 0.0


# Non-finite inputs: NaN and ±inf must never slip past a cap or a survival rule.
wild = st.floats(allow_nan=True, allow_infinity=True)
# Mostly plausible values with frequent non-finite ones, so that approvals stay reachable.
mixed = st.sampled_from([math.nan, math.inf, -math.inf]) | st.floats(min_value=1, max_value=1e5)
wild_portfolios = st.builds(
    PortfolioView,
    cash=mixed,
    total_value=mixed,
    open_exposure=mixed | st.just(0.0),
    open_market_ids=st.just(frozenset()),
    exposure_known=st.just(True),
)
wild_proposals = st.builds(
    lambda stake, side, p_cons: make_proposal(side=side, p_cons=p_cons).model_copy(
        update={"stake": stake}
    ),
    stake=mixed,
    side=st.sampled_from([Side.YES, Side.NO]),
    p_cons=st.floats(min_value=0.001, max_value=0.999),
)


@settings(max_examples=500)
@given(wild_proposals, deep_snapshots, wild_portfolios, open_survivals)
def test_risk_verdict_invariants_non_finite(proposal, snap, portfolio, survival):
    verdict, _ = ENGINE.evaluate(proposal, snap, portfolio, survival)
    assert math.isfinite(verdict.stake)
    _check_invariants(proposal, snap, portfolio, survival)


@given(
    total=wild,
    peak=wild,
    initial=wild,
    age=st.none() | wild,
    known=st.booleans(),
    halted=st.booleans(),
)
def test_survival_non_finite_inputs(total, peak, initial, age, known, halted):
    inp = SurvivalInputs(total, peak, initial, age, known, halted)
    try:
        a = assess_survival(inp, SurvivalConfig(), RiskConfig())
    except ValueError:
        # Only finite but non-positive reference values may raise.
        assert math.isfinite(peak) and math.isfinite(initial)
        assert peak <= 0 or initial <= 0
        return
    assert a.state in REACHABLE
    finite = all(math.isfinite(x) for x in (total, peak, initial))
    if halted:
        assert a.state is SurvivalState.HALTED
    elif not known:
        # Unknown exposure (DB error, missing price) observes; it never halts nor fails.
        assert a.state is SurvivalState.OBSERVING
    elif not finite:
        assert a.state is SurvivalState.HALTED
    if a.allow_new_positions:
        assert finite and age is not None and math.isfinite(age)

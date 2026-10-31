import pytest

from ambre.config.settings import RiskConfig, SurvivalConfig
from ambre.domain.models import SurvivalState
from ambre.survival_engine.survival import (
    FAILURE_REASON_PREFIX,
    SurvivalInputs,
    assess_survival,
    is_failure,
)

CFG = SurvivalConfig()
RISK = RiskConfig()


def _inputs(**kw):
    data = {
        "total_value": 1000.0,
        "peak_value": 1000.0,
        "initial_capital": 1000.0,
        "data_age_seconds": 60.0,
        "exposure_known": True,
        "halted": False,
    }
    data.update(kw)
    return SurvivalInputs(**data)


@pytest.mark.parametrize(
    ("kw", "state", "multiplier", "allow"),
    [
        # Rule 1 beats every other rule.
        (
            {"halted": True, "total_value": 100.0, "data_age_seconds": None},
            SurvivalState.HALTED,
            0.0,
            False,
        ),
        # Unknown exposure beats failure: a lower-bound value must never kill the simulation.
        ({"total_value": 100.0, "exposure_known": False}, SurvivalState.OBSERVING, 0.0, False),
        # Failure beats stale data and drawdown.
        ({"total_value": 500.0, "data_age_seconds": None}, SurvivalState.HALTED, 0.0, False),
        # Stale data beats drawdown.
        ({"total_value": 600.0, "data_age_seconds": None}, SurvivalState.OBSERVING, 0.0, False),
        ({"total_value": 600.0, "exposure_known": False}, SurvivalState.OBSERVING, 0.0, False),
        ({"total_value": 700.0}, SurvivalState.CRITICAL, 0.0, False),
        ({"total_value": 850.0}, SurvivalState.DEFENSIVE, 0.5, True),
        ({"total_value": 851.0}, SurvivalState.BALANCED, 1.0, True),
        ({"total_value": 1200.0}, SurvivalState.BALANCED, 1.0, True),
    ],
)
def test_survival_rules_in_order(kw, state, multiplier, allow):
    a = assess_survival(_inputs(**kw), CFG, RISK)
    assert a.state is state
    assert a.stake_multiplier == pytest.approx(multiplier)
    assert a.allow_new_positions is allow
    assert a.reasons


def test_survival_failure_reason():
    a = assess_survival(_inputs(total_value=500.0), CFG, RISK)
    assert "failure: portfolio value ≤ 50% of initial capital" in a.reasons


@pytest.mark.parametrize(
    "kw",
    [
        {"data_age_seconds": None},  # never synced
        {"data_age_seconds": 900.1},  # stale
        {"exposure_known": False},  # DB unreachable while computing exposure
    ],
)
def test_survival_stale_or_unknown_data_observing(kw):
    a = assess_survival(_inputs(**kw), CFG, RISK)
    assert a.state is SurvivalState.OBSERVING
    assert not a.allow_new_positions
    assert a.stake_multiplier == 0.0


def test_survival_data_age_boundary_is_fresh():
    assert (
        assess_survival(_inputs(data_age_seconds=900.0), CFG, RISK).state is SurvivalState.BALANCED
    )


def test_survival_rejects_invalid_reference_values():
    with pytest.raises(ValueError):
        assess_survival(_inputs(peak_value=0.0), CFG, RISK)
    with pytest.raises(ValueError):
        assess_survival(_inputs(initial_capital=0.0), CFG, RISK)


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize(
    "kw",
    [
        {"total_value": NAN},
        {"total_value": INF},
        {"peak_value": NAN},
        {"peak_value": INF},
        {"initial_capital": NAN},
        {"initial_capital": -INF},
    ],
)
def test_survival_non_finite_valuation_halts(kw):
    a = assess_survival(_inputs(**kw), CFG, RISK)
    assert a.state is SurvivalState.HALTED
    assert not a.allow_new_positions
    assert "invalid portfolio valuation" in a.reasons


@pytest.mark.parametrize("age", [NAN, INF])
def test_survival_non_finite_data_age_observing(age):
    a = assess_survival(_inputs(data_age_seconds=age), CFG, RISK)
    assert a.state is SurvivalState.OBSERVING
    assert not a.allow_new_positions


def test_survival_db_error_view_is_observing_not_halted():
    # PortfolioService.view on a DB error: NaN valuations with exposure_known=False.
    a = assess_survival(
        _inputs(total_value=NAN, peak_value=1000.0, exposure_known=False), CFG, RISK
    )
    assert a.state is SurvivalState.OBSERVING
    assert a.reasons == ["exposure unknown"]


def test_survival_missing_price_never_triggers_failure():
    # A missing price gives a lower-bound total (cash only) with exposure_known=False.
    a = assess_survival(_inputs(total_value=10.0, exposure_known=False), CFG, RISK)
    assert a.state is SurvivalState.OBSERVING
    assert not any(r.startswith(FAILURE_REASON_PREFIX) for r in a.reasons)


def test_survival_failure_beats_stale_data_and_is_tagged():
    a = assess_survival(_inputs(total_value=400.0, data_age_seconds=NAN), CFG, RISK)
    assert a.state is SurvivalState.HALTED
    assert is_failure(a)
    assert not is_failure(assess_survival(_inputs(halted=True), CFG, RISK))
    assert not is_failure(assess_survival(_inputs(total_value=NAN), CFG, RISK))
    assert not is_failure(assess_survival(_inputs(data_age_seconds=None), CFG, RISK))

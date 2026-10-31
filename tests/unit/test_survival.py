import pytest

from ambre.config.settings import RiskConfig, SurvivalConfig
from ambre.domain.models import SurvivalState
from ambre.survival_engine.survival import SurvivalInputs, assess_survival

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
        # Rule 2 beats stale data and drawdown.
        ({"total_value": 500.0, "data_age_seconds": None}, SurvivalState.HALTED, 0.0, False),
        # Rule 3 beats drawdown.
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

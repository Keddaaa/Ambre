import pytest
from pydantic import ValidationError

from ambre.config.settings import RiskConfig, Settings
from ambre.domain.models import ExecutionMode


def test_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    monkeypatch.delenv("ENABLE_MANIFOLD_ORDERS", raising=False)
    s = Settings(_env_file=None)
    assert s.execution_mode is ExecutionMode.PAPER
    assert s.enable_manifold_orders is False
    assert s.initial_capital == 1000.0


def test_api_key_does_not_enable_orders(monkeypatch):
    monkeypatch.delenv("ENABLE_MANIFOLD_ORDERS", raising=False)
    monkeypatch.setenv("MANIFOLD_API_KEY", "secret")
    s = Settings(_env_file=None)
    assert s.enable_manifold_orders is False
    assert "secret" not in repr(s)


def test_risk_values_from_yaml():
    r = Settings(_env_file=None).risk
    assert (
        r.max_position_fraction,
        r.max_total_exposure_fraction,
        r.kelly_fraction,
        r.minimum_edge,
        r.critical_drawdown,
    ) == (0.02, 0.20, 0.25, 0.03, 0.30)


def test_other_blocks_from_yaml():
    s = Settings(_env_file=None)
    assert s.survival.max_data_age_seconds == 900
    assert s.eligibility.min_unique_bettors == 5
    assert s.prediction.shrinkage_market_weight == 0.9


def test_env_overrides_top_level(monkeypatch):
    monkeypatch.setenv("INITIAL_CAPITAL", "500")
    assert Settings(_env_file=None).initial_capital == 500.0


def test_config_path_overridable(monkeypatch, tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("risk:\n  kelly_fraction: 0.1\n")
    monkeypatch.setenv("AMBRE_CONFIG", str(p))
    s = Settings(_env_file=None)
    assert s.risk.kelly_fraction == 0.1
    assert s.risk.max_position_fraction == 0.02


def test_invalid_fraction_rejected():
    with pytest.raises(ValidationError):
        RiskConfig(max_position_fraction=1.5)


# ------------------------------------------------------------------ forward-test model switch

import math  # noqa: E402
from pathlib import Path  # noqa: E402

from ambre.config.settings import PredictionConfig, RecalibrationConfig  # noqa: E402

FORWARD = Path(__file__).resolve().parents[2] / "configs" / "forward_test.yaml"


def _recal(**over):  # type: ignore[no-untyped-def]
    data = dict(a=-0.3, b=0.9, n_fit=100, fitted_on="test", min_age_days=2.0, max_age_days=4.0)
    data.update(over)
    return RecalibrationConfig(**data)


def test_the_default_model_is_unchanged_and_needs_no_recalibration():
    p = PredictionConfig()
    assert p.model == "base_rate_shrinkage" and p.recalibration is None


def test_the_recalibrated_model_cannot_be_selected_without_its_frozen_parameters():
    with pytest.raises(ValidationError):
        PredictionConfig(model="recalibrated_price")
    ok = PredictionConfig(model="recalibrated_price", recalibration=_recal())
    assert ok.recalibration is not None and ok.recalibration.b == 0.9


def test_an_unknown_model_name_is_rejected_so_a_typo_cannot_change_the_bets():
    with pytest.raises(ValidationError):
        PredictionConfig(model="recalibrated_prise", recalibration=_recal())


@pytest.mark.parametrize(
    "over",
    [
        {"b": 0.0},
        {"b": -1.0},
        {"a": math.nan},
        {"b": math.inf},
        {"min_age_days": 5.0, "max_age_days": 4.0},
        {"min_age_days": -1.0},
        {"uncertainty": 0.0},
        {"uncertainty": 0.6},
        {"n_fit": 0},
    ],
)
def test_invalid_recalibration_values_are_refused(over):
    with pytest.raises(ValidationError):
        _recal(**over)


def test_the_forward_test_config_is_paper_only_costly_and_never_riskier_than_the_default(
    monkeypatch,
):
    monkeypatch.setenv("AMBRE_CONFIG", str(FORWARD))
    for key in ("EXECUTION_MODE", "ENABLE_MANIFOLD_ORDERS"):
        monkeypatch.delenv(key, raising=False)
    fw = Settings(_env_file=None)
    monkeypatch.delenv("AMBRE_CONFIG")
    base = Settings(_env_file=None)
    assert fw.execution_mode is ExecutionMode.PAPER and fw.enable_manifold_orders is False
    assert fw.prediction.model == "recalibrated_price" and fw.prediction.recalibration
    # an explicit cost, and no limit looser than the default
    assert fw.risk.fee_rate >= 0.02 > base.risk.fee_rate
    assert fw.risk.kelly_fraction <= base.risk.kelly_fraction
    assert fw.risk.max_position_fraction <= base.risk.max_position_fraction
    assert fw.risk.max_total_exposure_fraction <= base.risk.max_total_exposure_fraction
    assert fw.risk.minimum_edge >= base.risk.minimum_edge
    assert fw.survival == base.survival and fw.eligibility == base.eligibility
    # the experiment collects nothing else: research and news stay off
    assert fw.intelligence.schedule_research is False and fw.intelligence.schedule_news is False

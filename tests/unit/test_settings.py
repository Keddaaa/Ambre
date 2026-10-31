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

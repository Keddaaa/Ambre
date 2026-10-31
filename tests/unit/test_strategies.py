import pytest
from builders import make_estimate, make_market, make_snapshot

from ambre.config.settings import RiskConfig
from ambre.domain.models import Side, StrategyName
from ambre.strategy_engine.strategies import NoTrade, ValueHunter
from ambre.value_engine.value import kelly_no, kelly_yes

RISK = RiskConfig()


def _evaluate(p, u, q, total_value=1000.0):
    return ValueHunter().evaluate(
        make_market(), make_snapshot(probability=q), make_estimate(p, u), total_value, RISK
    )


def test_value_hunter_no_trade_when_edge_within_uncertainty():
    assert _evaluate(0.51, 0.02, 0.5) is None
    assert _evaluate(0.49, 0.02, 0.5) is None
    assert _evaluate(0.52, 0.02, 0.5) is None  # p_cons == q: no edge
    assert _evaluate(0.5, 0.0, 0.5) is None


def test_value_hunter_sizes_with_fractional_kelly():
    prop = _evaluate(0.6, 0.02, 0.5)
    assert prop is not None
    assert prop.side is Side.YES
    assert prop.conservative_probability == pytest.approx(0.58)
    assert prop.stake == pytest.approx(0.25 * kelly_yes(0.58, 0.5) * 1000)
    assert prop.stake == pytest.approx(40)
    assert prop.market_id == "synthetic_m1"
    joined = " ".join(prop.reasons)
    for token in ("p=0.6", "q=0.5", "u=0.02", "YES"):
        assert token in joined
    assert "market moves past p_cons" in prop.invalidation
    assert "estimate uncertainty exceeds edge" in prop.invalidation


def test_value_hunter_no_side():
    prop = _evaluate(0.3, 0.05, 0.5)
    assert prop is not None
    assert prop.side is Side.NO
    # conservative_probability stays P(YES), pushed towards the market.
    assert prop.conservative_probability == pytest.approx(0.35)
    assert prop.stake == pytest.approx(0.25 * kelly_no(0.35, 0.5) * 1000)


def test_value_hunter_clamps_conservative_probability():
    prop = _evaluate(0.0, 0.0, 0.5)
    assert prop is not None
    assert prop.conservative_probability == pytest.approx(0.001)
    prop = _evaluate(1.0, 0.0, 0.5)
    assert prop is not None
    assert prop.conservative_probability == pytest.approx(0.999)


@pytest.mark.parametrize("q", [0.0, 1.0])
def test_value_hunter_degenerate_price_is_no_trade(q):
    assert _evaluate(0.5, 0.02, q) is None


def test_no_trade_never_trades():
    s = NoTrade()
    assert s.name is StrategyName.NO_TRADE
    assert ValueHunter().name is StrategyName.VALUE_HUNTER
    assert s.evaluate(make_market(), make_snapshot(), make_estimate(0.9, 0.0), 1000.0, RISK) is None

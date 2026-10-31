import pytest

from ambre.domain.models import Side
from ambre.value_engine.cpmm import cpmm_fill
from ambre.value_engine.value import assess_fill, ev_ideal, kelly_no, kelly_yes


def test_ev_and_kelly():
    assert ev_ideal(0.6, 0.5, Side.YES) == pytest.approx(0.1)
    assert ev_ideal(0.6, 0.5, Side.NO) == pytest.approx(-0.1)
    assert kelly_yes(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_no(0.4, 0.5) == pytest.approx(0.2)
    assert kelly_yes(0.4, 0.5) == 0.0
    assert kelly_no(0.6, 0.5) == 0.0


def test_assess_fill_includes_impact_and_fees():
    fill = cpmm_fill(100, 100, 0.5, 10, Side.YES)
    v = assess_fill(0.6, fill, fee_rate=0.0)
    assert v.expected_value == pytest.approx(1.4545, abs=1e-4)
    assert v.cost == pytest.approx(10)
    assert v.ev_per_mana == pytest.approx(v.expected_value / 10)
    v2 = assess_fill(0.6, fill, fee_rate=0.05)
    assert v2.expected_value < v.expected_value
    assert v2.cost == pytest.approx(10.5)

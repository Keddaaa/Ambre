import math

import pytest

from ambre.domain.models import Side
from ambre.value_engine.cpmm import cpmm_fill, cpmm_probability, cpmm_shares


def test_prob_symmetric_pool():
    assert cpmm_probability(100, 100, 0.5) == pytest.approx(0.5)


def test_yes_fill_10_on_100_100():
    f = cpmm_fill(100, 100, 0.5, 10, Side.YES)
    assert f.shares == pytest.approx(19.0909, abs=1e-4)
    assert f.avg_price == pytest.approx(0.5238, abs=1e-4)
    assert f.prob_before == pytest.approx(0.5)
    assert f.prob_after == pytest.approx(0.5475, abs=1e-4)


def test_no_fill_is_mirror():
    f = cpmm_fill(100, 100, 0.5, 10, Side.NO)
    assert f.shares == pytest.approx(19.0909, abs=1e-4)
    assert f.prob_after < f.prob_before


def test_extreme_price_fill_finite():
    f = cpmm_fill(10_000, 101, 0.5, 20, Side.YES)
    assert math.isfinite(f.shares) and f.shares > 0


@pytest.mark.parametrize(
    ("y", "n", "p", "amount"),
    [
        (0, 100, 0.5, 10),
        (100, -1, 0.5, 10),
        (100, 100, 0.0, 10),
        (100, 100, 1.0, 10),
        (100, 100, 0.5, 0),
        (100, 100, 0.5, -5),
    ],
)
def test_invalid_inputs_raise(y, n, p, amount):
    with pytest.raises(ValueError):
        cpmm_shares(y, n, p, amount, Side.YES)
    with pytest.raises(ValueError):
        cpmm_fill(y, n, p, amount, Side.NO)


def test_probability_invalid_inputs_raise():
    with pytest.raises(ValueError):
        cpmm_probability(0, 100, 0.5)
    with pytest.raises(ValueError):
        cpmm_probability(100, 100, 1.0)

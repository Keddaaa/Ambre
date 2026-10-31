import math

from hypothesis import given
from hypothesis import strategies as st

from ambre.domain.models import Side
from ambre.value_engine.cpmm import _buy, cpmm_fill, cpmm_probability
from ambre.value_engine.value import kelly_no, kelly_yes

pools = st.floats(min_value=1, max_value=1e6)
probs = st.floats(min_value=0.01, max_value=0.99)
amounts = st.floats(min_value=0.01, max_value=1e4)
sides = st.sampled_from([Side.YES, Side.NO])


@given(pools, pools, probs)
def test_probability_in_open_unit_interval(y, n, p):
    assert 0 < cpmm_probability(y, n, p) < 1


# Float limit: with p near 0.01 or 0.99 the surviving pool is (n / (n + amount)) ** (1 / p)
# times y, which underflows to 0.0 when amount >> pool. The fill amount is therefore
# bounded to 100x the smaller pool (well past any bet the risk engine would size).
fills = st.tuples(pools, pools, probs, sides).flatmap(
    lambda t: st.tuples(
        st.just(t[0]),
        st.just(t[1]),
        st.just(t[2]),
        st.floats(min_value=0.01, max_value=min(1e4, 100 * min(t[0], t[1]))),
        st.just(t[3]),
    )
)


@given(fills)
def test_fill_properties(args):
    y, n, p, amount, side = args
    f = cpmm_fill(y, n, p, amount, side)
    assert f.shares > 0
    assert 0 < f.avg_price < 1
    if side is Side.YES:
        assert f.prob_after > f.prob_before
    else:
        assert f.prob_after < f.prob_before


@given(fills)
def test_k_invariant_conserved(args):
    y, n, p, amount, side = args
    # Uses the internal pool update: rebuilding the surviving pool as
    # y + amount - shares loses it to cancellation when it is tiny.
    _, y2, n2 = _buy(y, n, p, amount, side)
    k_before = y**p * n ** (1 - p)
    k_after = y2**p * n2 ** (1 - p)
    assert math.isclose(k_after, k_before, rel_tol=1e-9)


@given(st.floats(0.001, 0.999), st.floats(0.001, 0.999))
def test_kelly_bounds(p, q):
    assert 0 <= kelly_yes(p, q) <= 1
    assert 0 <= kelly_no(p, q) <= 1

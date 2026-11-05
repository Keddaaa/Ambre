"""Recalibration of a market price (pure, no I/O).

A market price ``q`` can be systematically too confident or not confident enough. The simplest
correction fits ``P(YES) = sigmoid(a + b * logit(q))`` on past resolved markets: ``a = 0, b = 1``
is the price itself, ``b < 1`` pulls prices towards the middle, ``b > 1`` pushes them out.

The fit is a ridge-regularised logistic regression whose prior is the identity ``(a, b) = (0, 1)``,
so a small sample stays close to the market price instead of chasing noise, and it abstains
(returns ``None``) below a minimum sample. It uses only the standard library.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

CLIP = 0.02  # prices beyond [0.02, 0.98] are clipped for the logit: it stays finite and stable
DEFAULT_MIN_N = 30
DEFAULT_L2 = 1.0
_MAX_ITER = 100
_TOL = 1e-10


def _check_probability(p: float) -> None:
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p!r}")


def logit(p: float) -> float:
    _check_probability(p)
    q = min(max(p, CLIP), 1.0 - CLIP)
    return math.log(q / (1.0 - q))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass(frozen=True)
class PlattCalibrator:
    a: float
    b: float
    n: int

    def predict(self, probability: float) -> float:
        return _sigmoid(self.a + self.b * logit(probability))

    @classmethod
    def fit(
        cls,
        probs: Sequence[float],
        outcomes: Sequence[bool],
        *,
        min_n: int = DEFAULT_MIN_N,
        l2: float = DEFAULT_L2,
    ) -> "PlattCalibrator | None":
        if len(probs) != len(outcomes):
            raise ValueError("probabilities and outcomes must have the same length")
        xs = [logit(p) for p in probs]  # also validates every probability
        n = len(xs)
        if n < min_n:
            return None
        ys = [1.0 if o else 0.0 for o in outcomes]
        a, b = 0.0, 1.0
        for _ in range(_MAX_ITER):
            ga, gb = l2 * a, l2 * (b - 1.0)
            haa, hab, hbb = l2, 0.0, l2
            for x, y in zip(xs, ys, strict=True):
                p = _sigmoid(a + b * x)
                w = p * (1.0 - p)
                ga += p - y
                gb += (p - y) * x
                haa += w
                hab += w * x
                hbb += w * x * x
            det = haa * hbb - hab * hab
            da = (hbb * ga - hab * gb) / det
            db = (haa * gb - hab * ga) / det
            a, b = a - da, b - db
            if abs(da) < _TOL and abs(db) < _TOL:
                break
        return cls(a, b, n)

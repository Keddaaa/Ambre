"""Logistic model anchored on the market price (pure, no I/O, standard library only).

``P(YES) = sigmoid(a + b * logit(q) + sum(w_j * x_j))``. The ridge penalty pulls ``(a, b, w)``
towards ``(0, 1, 0)``, which is the price itself: without evidence the model stays the market.
Features are standardised on the training sample; a missing value takes the training mean, so it
says nothing, and the share of missing values is kept with the model.

It is a research model (ADR-022): nothing on the betting path reads it.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

LOGIT_CLIP = 0.005
#: a forecast never claims certainty, whatever an out-of-range feature says
MAX_ABS_Z = 9.0
DEFAULT_MIN_N = 30
_MAX_ITER = 60
_TOL = 1e-8

Row = Mapping[str, float | int | None]


def anchored_logit(p: float) -> float:
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p!r}")
    q = min(max(p, LOGIT_CLIP), 1.0 - LOGIT_CLIP)
    return math.log(q / (1.0 - q))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(vector)
    a = [row[:] + [v] for row, v in zip(matrix, vector, strict=True)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            factor = a[r][col] / a[col][col]
            if factor:
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        out[r] = (a[r][n] - sum(a[r][c] * out[c] for c in range(r + 1, n))) / a[r][r]
    return out


def _objective(theta: Sequence[float], prior: Sequence[float], xs: Sequence[Sequence[float]],
               ys: Sequence[float], l2: float) -> float:  # fmt: skip
    total = 0.5 * l2 * sum((t - p) ** 2 for t, p in zip(theta, prior, strict=True))
    for x, y in zip(xs, ys, strict=True):
        z = sum(t * v for t, v in zip(theta, x, strict=True))
        # log(1 + exp(z)) - y * z, stable on both sides
        total += max(z, 0.0) + math.log1p(math.exp(-abs(z))) - y * z
    return total


def _fit_theta(xs: Sequence[Sequence[float]], ys: Sequence[float], l2: float) -> list[float]:
    d = len(xs[0])
    prior = [0.0, 1.0] + [0.0] * (d - 2)
    theta = prior[:]
    current = _objective(theta, prior, xs, ys, l2)
    for _ in range(_MAX_ITER):
        grad = [l2 * (t - p) for t, p in zip(theta, prior, strict=True)]
        hess = [[l2 if i == j else 0.0 for j in range(d)] for i in range(d)]
        for x, y in zip(xs, ys, strict=True):
            p = _sigmoid(sum(t * v for t, v in zip(theta, x, strict=True)))
            w = p * (1.0 - p)
            r = p - y
            for i in range(d):
                grad[i] += r * x[i]
                wi = w * x[i]
                row = hess[i]
                for j in range(i, d):
                    row[j] += wi * x[j]
        for i in range(d):
            for j in range(i):
                hess[i][j] = hess[j][i]
        step = _solve(hess, grad)
        scale = 1.0
        while True:  # backtracking: a full Newton step can overshoot on a separable sample
            trial = [t - scale * s for t, s in zip(theta, step, strict=True)]
            value = _objective(trial, prior, xs, ys, l2)
            if value <= current or scale < 1e-6:
                break
            scale /= 2.0
        theta = trial
        converged = max(abs(scale * s) for s in step) < _TOL
        current = value
        if converged:
            break
    return theta


@dataclass(frozen=True)
class AnchoredLogistic:
    names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    a: float
    b: float
    weights: tuple[float, ...]
    n: int
    l2: float
    #: per feature, the share of training rows where it was missing
    missing_rate: dict[str, float]

    def predict(self, row: Row, market_probability: float) -> float:
        z = self.a + self.b * anchored_logit(market_probability)
        for name, mean, scale, weight in zip(
            self.names, self.means, self.scales, self.weights, strict=True
        ):
            value = row.get(name)
            if value is not None:
                z += weight * (float(value) - mean) / scale
        return _sigmoid(min(max(z, -MAX_ABS_Z), MAX_ABS_Z))

    @classmethod
    def fit(
        cls,
        rows: Sequence[Row],
        market_probabilities: Sequence[float],
        outcomes: Sequence[bool],
        names: Sequence[str],
        *,
        l2: float,
        min_n: int = DEFAULT_MIN_N,
    ) -> "AnchoredLogistic | None":
        if not len(rows) == len(market_probabilities) == len(outcomes):
            raise ValueError("rows, prices and outcomes must have the same length")
        if len(rows) < min_n:
            return None
        kept: list[tuple[str, float, float]] = []
        missing: dict[str, float] = {}
        for name in names:
            values = [float(v) for r in rows if (v := r.get(name)) is not None]
            if len(values) < 2:
                continue
            mean = sum(values) / len(values)
            scale = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
            if scale <= 1e-12:
                continue  # constant here, as the age is at a fixed horizon
            kept.append((name, mean, scale))
            missing[name] = 1.0 - len(values) / len(rows)
        xs: list[list[float]] = []
        for row, q in zip(rows, market_probabilities, strict=True):
            x = [1.0, anchored_logit(q)]
            for name, mean, scale in kept:
                value = row.get(name)
                x.append(0.0 if value is None else (float(value) - mean) / scale)
            xs.append(x)
        theta = _fit_theta(xs, [1.0 if o else 0.0 for o in outcomes], l2)
        return cls(
            names=tuple(k[0] for k in kept),
            means=tuple(k[1] for k in kept),
            scales=tuple(k[2] for k in kept),
            a=theta[0],
            b=theta[1],
            weights=tuple(theta[2:]),
            n=len(rows),
            l2=l2,
            missing_rate=missing,
        )

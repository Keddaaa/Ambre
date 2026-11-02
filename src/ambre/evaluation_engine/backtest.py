"""Chronological walk-forward backtest of the base-rate baseline (pure, no I/O).

For every resolved YES/NO market there is one observation, taken at ``decision_at``
(ADR-007). The baseline is refitted for each observation using **only** markets whose
resolution was already known strictly before that instant, so no outcome leaks into
its own forecast. The model and the market price are then scored on the same markets
with the existing metrics. Nothing here proves an edge: a small sample says nothing
(``report.sufficient_sample``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ambre.config.settings import PredictionConfig
from ambre.evaluation_engine.metrics import EvaluationReport, ScoredForecast, evaluate
from ambre.feature_engine.features import FeatureVector
from ambre.prediction_engine.baselines import BaseRateShrinkage


@dataclass(frozen=True)
class BacktestMarket:
    """A market as seen at one decision instant, with its (possibly unknown) resolution."""

    market_id: str
    decision_at: datetime
    market_probability: float
    #: ``None`` when the market is unresolved or its resolution date is unknown.
    resolution_at: datetime | None
    #: ``True`` = YES, ``False`` = NO, ``None`` = unresolved or not YES/NO.
    outcome: bool | None


@dataclass(frozen=True)
class BacktestFold:
    market_id: str
    decision_at: datetime
    #: Resolved markets known strictly before ``decision_at`` that fitted the baseline.
    n_train: int
    market_probability: float
    estimated_probability: float
    outcome: bool


@dataclass(frozen=True)
class BacktestResult:
    report: EvaluationReport
    folds: list[BacktestFold]


def _scorable(m: BacktestMarket) -> bool:
    return m.outcome is not None and m.resolution_at is not None and m.decision_at < m.resolution_at


def run_backtest(markets: Sequence[BacktestMarket], cfg: PredictionConfig) -> BacktestResult:
    """Walk forward in decision order; see the module docstring for the no-leak rule."""
    known = [m for m in markets if m.outcome is not None and m.resolution_at is not None]
    folds: list[BacktestFold] = []
    for m in sorted(
        (m for m in markets if _scorable(m)), key=lambda x: (x.decision_at, x.market_id)
    ):
        train = [
            "YES" if k.outcome else "NO"
            for k in known
            if k.resolution_at is not None and k.resolution_at < m.decision_at
        ]
        model = BaseRateShrinkage.fit(train, cfg)
        estimate = model.predict(
            FeatureVector(
                market_id=m.market_id,
                available_at=m.decision_at,
                values={"market_probability": m.market_probability},
            )
        )
        assert m.outcome is not None  # narrowed by _scorable
        folds.append(
            BacktestFold(
                market_id=m.market_id,
                decision_at=m.decision_at,
                n_train=len(train),
                market_probability=m.market_probability,
                estimated_probability=estimate.probability,
                outcome=m.outcome,
            )
        )
    report = evaluate(
        [ScoredForecast(f.market_probability, f.estimated_probability, f.outcome) for f in folds]
    )
    return BacktestResult(report=report, folds=folds)

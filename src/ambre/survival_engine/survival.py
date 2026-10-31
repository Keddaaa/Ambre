"""Survival engine: maps portfolio health to an operating state (pure, no I/O)."""

import math
from dataclasses import dataclass

from ambre.config.settings import RiskConfig, SurvivalConfig
from ambre.domain.models import SurvivalAssessment, SurvivalState

FAILURE_REASON_PREFIX = "failure:"


@dataclass(frozen=True)
class SurvivalInputs:
    total_value: float
    peak_value: float
    initial_capital: float
    data_age_seconds: float | None  # None: no successful sync yet
    exposure_known: bool
    halted: bool


def is_failure(assessment: SurvivalAssessment) -> bool:
    """True only when the portfolio failure rule fired (the simulation must die)."""
    return assessment.state is SurvivalState.HALTED and any(
        r.startswith(FAILURE_REASON_PREFIX) for r in assessment.reasons
    )


def _blocked(state: SurvivalState, reason: str) -> SurvivalAssessment:
    return SurvivalAssessment(
        state=state, stake_multiplier=0.0, allow_new_positions=False, reasons=[reason]
    )


def assess_survival(
    inp: SurvivalInputs, cfg: SurvivalConfig, risk: RiskConfig
) -> SurvivalAssessment:
    """Apply the survival rules in order; the first rule that matches wins.

    1. manual halt -> HALTED;
    2. unknown exposure -> OBSERVING (a DB error or a missing price gives a NaN or
       lower-bound valuation, which must never trigger the failure rule);
    3. non-finite valuation -> HALTED;
    4. failure (value <= failure fraction of initial capital) -> HALTED, reason
       prefixed with ``FAILURE_REASON_PREFIX`` (see ``is_failure``);
    5. data age missing, non-finite, negative or too old -> OBSERVING;
    6. drawdown >= critical -> CRITICAL; 7. >= defensive -> DEFENSIVE; 8. BALANCED.
    """
    if inp.halted:
        return _blocked(SurvivalState.HALTED, "simulation halted")
    if not inp.exposure_known:
        return _blocked(SurvivalState.OBSERVING, "exposure unknown")
    if not all(math.isfinite(x) for x in (inp.total_value, inp.peak_value, inp.initial_capital)):
        return _blocked(SurvivalState.HALTED, "invalid portfolio valuation")
    if inp.peak_value <= 0 or inp.initial_capital <= 0:
        raise ValueError("peak_value and initial_capital must be > 0")
    if inp.total_value <= cfg.failure_value_fraction * inp.initial_capital:
        pct = f"{cfg.failure_value_fraction:.0%}"
        return _blocked(
            SurvivalState.HALTED,
            f"{FAILURE_REASON_PREFIX} portfolio value ≤ {pct} of initial capital",
        )
    if inp.data_age_seconds is None:
        return _blocked(SurvivalState.OBSERVING, "no successful data sync yet")
    if not (math.isfinite(inp.data_age_seconds) and inp.data_age_seconds >= 0):
        return _blocked(SurvivalState.OBSERVING, f"invalid data age {inp.data_age_seconds}")
    if inp.data_age_seconds > cfg.max_data_age_seconds:
        return _blocked(
            SurvivalState.OBSERVING,
            f"data age {inp.data_age_seconds:.0f}s > max {cfg.max_data_age_seconds}s",
        )
    drawdown = 1 - inp.total_value / inp.peak_value
    if drawdown >= risk.critical_drawdown:
        return _blocked(
            SurvivalState.CRITICAL, f"drawdown {drawdown:.2%} ≥ {risk.critical_drawdown:.0%}"
        )
    if drawdown >= cfg.defensive_drawdown:
        return SurvivalAssessment(
            state=SurvivalState.DEFENSIVE,
            stake_multiplier=cfg.defensive_stake_multiplier,
            allow_new_positions=True,
            reasons=[f"drawdown {drawdown:.2%} ≥ {cfg.defensive_drawdown:.0%}"],
        )
    return SurvivalAssessment(
        state=SurvivalState.BALANCED,
        stake_multiplier=1.0,
        allow_new_positions=True,
        reasons=[f"drawdown {drawdown:.2%} below defensive threshold"],
    )

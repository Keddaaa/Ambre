"""Survival engine: maps portfolio health to an operating state (pure, no I/O)."""

from dataclasses import dataclass

from ambre.config.settings import RiskConfig, SurvivalConfig
from ambre.domain.models import SurvivalAssessment, SurvivalState


@dataclass(frozen=True)
class SurvivalInputs:
    total_value: float
    peak_value: float
    initial_capital: float
    data_age_seconds: float | None  # None: no successful sync yet
    exposure_known: bool
    halted: bool


def _blocked(state: SurvivalState, reason: str) -> SurvivalAssessment:
    return SurvivalAssessment(
        state=state, stake_multiplier=0.0, allow_new_positions=False, reasons=[reason]
    )


def assess_survival(
    inp: SurvivalInputs, cfg: SurvivalConfig, risk: RiskConfig
) -> SurvivalAssessment:
    """Apply the survival rules in order; the first rule that matches wins."""
    if inp.peak_value <= 0 or inp.initial_capital <= 0:
        raise ValueError("peak_value and initial_capital must be > 0")
    if inp.halted:
        return _blocked(SurvivalState.HALTED, "simulation halted")
    if inp.total_value <= cfg.failure_value_fraction * inp.initial_capital:
        pct = f"{cfg.failure_value_fraction:.0%}"
        return _blocked(
            SurvivalState.HALTED, f"failure: portfolio value ≤ {pct} of initial capital"
        )
    if not inp.exposure_known:
        return _blocked(SurvivalState.OBSERVING, "exposure unknown")
    if inp.data_age_seconds is None:
        return _blocked(SurvivalState.OBSERVING, "no successful data sync yet")
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

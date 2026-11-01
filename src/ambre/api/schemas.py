"""Pydantic response/request models of the read API. Nullable DB columns stay Optional."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ambre.config.settings import RiskConfig, SurvivalConfig


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthOut(_Out):
    status: str
    db_ok: bool
    version: str


class SystemOut(_Out):
    execution_mode: str
    orders_enabled: bool
    halted: bool
    halt_reason: str | None
    last_sync_at: datetime | None
    last_sync_error: str | None
    data_age_seconds: float | None
    last_cycle_at: datetime | None
    decisions_total: int
    active_strategy: str
    risk: RiskConfig
    survival: SurvivalConfig


class MarketRowOut(_Out):
    id: str
    question: str
    url: str
    close_at: datetime | None
    is_resolved: bool
    resolution: str | None
    probability: float | None
    total_liquidity: float | None
    unique_bettor_count: int | None
    observed_at: datetime | None
    ambre_estimate: float | None
    ambre_uncertainty: float | None
    ambre_ev_per_mana: float | None
    last_action: str | None


class MarketsPage(_Out):
    items: list[MarketRowOut]
    total: int


class MarketOut(_Out):
    id: str
    question: str
    slug: str
    url: str
    creator_username: str
    mechanism: str
    created_at: datetime
    close_at: datetime | None
    is_resolved: bool
    resolution: str | None
    resolution_probability: float | None
    resolution_at: datetime | None
    group_slugs: list[str]


class SnapshotOut(_Out):
    id: int
    probability: float
    pool_yes: float
    pool_no: float
    total_liquidity: float | None
    volume: float | None
    volume_24h: float | None
    unique_bettor_count: int | None
    observed_at: datetime
    event_at: datetime | None
    source_id: str


class DecisionOut(_Out):
    decision_id: str
    market_id: str
    market_question: str | None = None
    created_at: datetime
    model_version: str
    strategy: str
    market_probability: float
    estimated_probability: float
    conservative_probability: float | None
    uncertainty: float
    expected_value: float | None
    ev_per_mana: float | None
    action: str
    # Proposed stake; only placed when execution_status == "SIMULATED".
    stake: float
    shares: float
    avg_price: float | None
    reasons: list[str]
    invalidation: list[str]
    source_ids: list[str]
    survival_state: str
    risk_reasons: list[str]
    execution_status: str
    outcome: str | None
    payout: float | None
    settled_at: datetime | None


class DecisionsPage(_Out):
    items: list[DecisionOut]
    total: int


class MarketDetailOut(_Out):
    market: MarketOut
    snapshots: list[SnapshotOut]
    decisions: list[DecisionOut]


class SimulationOut(_Out):
    id: str
    name: str
    status: str
    initial_capital: float
    created_at: datetime


class PositionOut(_Out):
    market_id: str
    question: str | None
    side: str
    shares: float
    cost: float
    opened_at: datetime
    current_price: float | None
    current_value: float | None


class PortfolioOut(_Out):
    simulation: SimulationOut
    cash: float
    positions_value: float
    total_value: float
    exposure_known: bool
    total_value_is_lower_bound: bool
    peak_value: float
    drawdown: float | None  # null when exposure is unknown (total_value is only a lower bound)
    survival_state: str | None  # from the latest portfolio snapshot, null if none yet
    survival_reasons: list[str]
    open_positions: list[PositionOut]


class PortfolioPointOut(_Out):
    at: datetime
    cash: float
    positions_value: float
    total_value: float
    peak_value: float
    drawdown: float
    survival_state: str
    survival_reasons: list[str]


class HaltIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=500)


class ResumeIn(BaseModel):
    """Explicit body so resume is never a CORS "simple" request (JSON content type needed)."""

    confirm: Literal[True]


class ControlOut(_Out):
    halted: bool
    reason: str | None
    updated_at: datetime | None

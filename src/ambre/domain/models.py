"""Shared domain contracts: enums and frozen Pydantic models."""

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class ExecutionMode(StrEnum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    MANIFOLD_MANA = "MANIFOLD_MANA"


class Side(StrEnum):
    YES = "YES"
    NO = "NO"


class Action(StrEnum):
    YES = "YES"
    NO = "NO"
    NO_TRADE = "NO_TRADE"


class SurvivalState(StrEnum):
    """Survival states of the engine.

    Only BALANCED, DEFENSIVE, OBSERVING, CRITICAL and HALTED are reachable in v0.1.
    """

    EXPLORING = "EXPLORING"
    EXPLOITING = "EXPLOITING"
    BALANCED = "BALANCED"
    DEFENSIVE = "DEFENSIVE"
    RECOVERING = "RECOVERING"
    OBSERVING = "OBSERVING"
    CRITICAL = "CRITICAL"
    HALTED = "HALTED"


class ExecutionStatus(StrEnum):
    SIMULATED = "SIMULATED"
    REJECTED = "REJECTED"
    NOT_EXECUTED = "NOT_EXECUTED"


class StrategyName(StrEnum):
    VALUE_HUNTER = "VALUE_HUNTER"
    NO_TRADE = "NO_TRADE"


class SimulationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEAD = "DEAD"
    ARCHIVED = "ARCHIVED"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class BinaryMarket(_Frozen):
    id: str
    question: str
    slug: str
    url: str
    creator_id: str
    creator_username: str
    mechanism: str
    created_at: AwareDatetime
    close_at: AwareDatetime | None
    is_resolved: bool
    resolution: str | None
    resolution_probability: Probability | None
    resolution_at: AwareDatetime | None
    token: str | None
    group_slugs: list[str] = Field(default_factory=list)


class MarketSnapshot(_Frozen):
    market_id: str
    probability: Probability
    pool_yes: float = Field(gt=0)
    pool_no: float = Field(gt=0)
    p: float = Field(gt=0, lt=1)
    total_liquidity: float | None
    volume: float | None
    volume_24h: float | None
    unique_bettor_count: int | None
    observed_at: AwareDatetime
    event_at: AwareDatetime | None
    source_id: str


class Estimate(_Frozen):
    probability: Probability
    uncertainty: float = Field(ge=0, le=0.5)
    model_version: str
    inputs: dict[str, float | int | str | None]


class Proposal(_Frozen):
    """A trade proposal.

    `conservative_probability` is always P(YES), whatever the side. The risk
    engine derives the probability of the chosen side from it later
    (P(NO) = 1 - conservative_probability for side NO).
    """

    market_id: str
    side: Side
    stake: float = Field(ge=0)
    conservative_probability: Probability
    estimate: Estimate
    reasons: list[str]
    invalidation: list[str]


class RiskVerdict(_Frozen):
    approved: bool
    stake: float = Field(ge=0)
    reasons: list[str]


class SurvivalAssessment(_Frozen):
    state: SurvivalState
    stake_multiplier: float = Field(ge=0, le=1)
    allow_new_positions: bool
    reasons: list[str]

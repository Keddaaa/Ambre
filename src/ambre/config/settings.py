import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ambre.domain.models import ExecutionMode

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "default.yaml"


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_position_fraction: float = Field(default=0.02, gt=0, le=1)
    max_total_exposure_fraction: float = Field(default=0.20, gt=0, le=1)
    kelly_fraction: float = Field(default=0.25, gt=0, le=1)
    minimum_edge: float = Field(default=0.03, gt=0, le=1)
    critical_drawdown: float = Field(default=0.30, gt=0, le=1)
    min_stake: float = Field(default=1.0, gt=0)
    fee_rate: float = Field(default=0.0, ge=0, lt=1)


class SurvivalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    defensive_drawdown: float = Field(default=0.15, gt=0, le=1)
    defensive_stake_multiplier: float = Field(default=0.5, gt=0, le=1)
    failure_value_fraction: float = Field(default=0.50, gt=0, le=1)
    max_data_age_seconds: int = Field(default=900, gt=0)


class EligibilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_total_liquidity: float = Field(default=100.0, ge=0)
    min_unique_bettors: int = Field(default=5, ge=0)
    min_seconds_to_close: int = Field(default=3600, ge=0)


class PredictionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    shrinkage_market_weight: float = Field(default=0.9, ge=0, le=1)
    uncertainty_floor: float = Field(default=0.02, ge=0, le=1)


def _load_yaml_block(name: str) -> dict[str, Any]:
    path = Path(os.environ.get("AMBRE_CONFIG") or DEFAULT_CONFIG_PATH)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = data.get(name) or {}
    if not isinstance(block, dict):
        raise ValueError(f"config block {name!r} in {path} must be a mapping")
    return block


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    execution_mode: ExecutionMode = ExecutionMode.PAPER
    enable_manifold_orders: bool = False
    manifold_api_key: SecretStr | None = None
    manifold_base_url: str = "https://api.manifold.markets"
    database_url: str = "sqlite:///./var/ambre.db"
    initial_capital: float = Field(default=1000.0, gt=0)
    cors_origins: list[str] = ["http://localhost:3000"]
    max_markets_per_cycle: int = Field(default=200, gt=0)

    risk: RiskConfig = Field(default_factory=lambda: RiskConfig(**_load_yaml_block("risk")))
    survival: SurvivalConfig = Field(
        default_factory=lambda: SurvivalConfig(**_load_yaml_block("survival"))
    )
    eligibility: EligibilityConfig = Field(
        default_factory=lambda: EligibilityConfig(**_load_yaml_block("eligibility"))
    )
    prediction: PredictionConfig = Field(
        default_factory=lambda: PredictionConfig(**_load_yaml_block("prediction"))
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

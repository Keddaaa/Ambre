"""Synthetic domain-object builders for pure-core tests (no Manifold data involved)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fakes import FakeManifoldClient
from sqlalchemy.orm import Session, sessionmaker

from ambre.config.settings import Settings
from ambre.data_engine.sync import MarketSyncService
from ambre.domain.models import (
    BinaryMarket,
    Estimate,
    ExecutionMode,
    MarketSnapshot,
    Proposal,
    Side,
    SurvivalAssessment,
    SurvivalState,
)
from ambre.feature_engine.features import FeatureVector
from ambre.pipeline import CycleReport, run_cycle
from ambre.risk_engine.risk import PortfolioView

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def make_market(**overrides: Any) -> BinaryMarket:
    data: dict[str, Any] = {
        "id": "synthetic_m1",
        "question": "Synthetic question?",
        "slug": "synthetic-question",
        "url": "https://manifold.markets/synthetic/synthetic-question",
        "creator_id": "synthetic_creator",
        "creator_username": "synthetic",
        "mechanism": "cpmm-1",
        "created_at": NOW - timedelta(days=10),
        "close_at": NOW + timedelta(days=30),
        "is_resolved": False,
        "resolution": None,
        "resolution_probability": None,
        "resolution_at": None,
        "token": "MANA",
    }
    data.update(overrides)
    return BinaryMarket(**data)


def make_snapshot(**overrides: Any) -> MarketSnapshot:
    data: dict[str, Any] = {
        "market_id": "synthetic_m1",
        "probability": 0.5,
        "pool_yes": 100.0,
        "pool_no": 100.0,
        "p": 0.5,
        "total_liquidity": 500.0,
        "volume": 1000.0,
        "volume_24h": 50.0,
        "unique_bettor_count": 20,
        "observed_at": NOW - timedelta(minutes=1),
        "event_at": NOW - timedelta(minutes=2),
        "source_id": "synthetic_source",
    }
    data.update(overrides)
    return MarketSnapshot(**data)


def make_feature_vector(q: float, market_id: str = "synthetic_m1") -> FeatureVector:
    return FeatureVector(
        market_id=market_id,
        available_at=NOW,
        values={
            "market_probability": q,
            "total_liquidity": 500.0,
            "volume_24h": 50.0,
            "unique_bettor_count": 20,
            "seconds_to_close": 86400.0,
            "seconds_since_creation": 86400.0,
        },
    )


def make_estimate(p: float, u: float = 0.02) -> Estimate:
    return Estimate(probability=p, uncertainty=u, model_version="synthetic", inputs={})


def make_proposal(
    stake: float = 40.0,
    side: Side = Side.YES,
    p_cons: float = 0.58,
    market_id: str = "synthetic_m1",
) -> Proposal:
    return Proposal(
        market_id=market_id,
        side=side,
        stake=stake,
        conservative_probability=p_cons,
        estimate=make_estimate(0.6),
        reasons=[],
        invalidation=[],
    )


def make_portfolio(**overrides: Any) -> PortfolioView:
    data: dict[str, Any] = {
        "cash": 1000.0,
        "total_value": 1000.0,
        "open_exposure": 0.0,
        "open_market_ids": frozenset(),
        "exposure_known": True,
    }
    data.update(overrides)
    return PortfolioView(**data)


def balanced() -> SurvivalAssessment:
    return SurvivalAssessment(
        state=SurvivalState.BALANCED, stake_multiplier=1.0, allow_new_positions=True, reasons=[]
    )


# --- pipeline helpers shared by the e2e and API tests
T_SYNC = datetime(2026, 10, 31, 14, 5, tzinfo=UTC)  # just after the fixture's last update
T_CYCLE = T_SYNC + timedelta(minutes=1)
SETTINGS = Settings(database_url="sqlite://", execution_mode=ExecutionMode.PAPER)


def sync(factory: sessionmaker[Session], markets: list[dict[str, Any]], at: datetime) -> None:
    service = MarketSyncService(FakeManifoldClient(markets), factory, lambda: at)  # type: ignore[arg-type]
    report = asyncio.run(service.sync(max_pages=5, page_size=1000))
    assert report.completed and report.new_snapshots > 0


def cycle(
    factory: sessionmaker[Session], now: datetime = T_CYCLE, settings: Settings = SETTINGS
) -> CycleReport:
    with factory() as s:
        report = run_cycle(s, settings, now)
        s.commit()
    return report

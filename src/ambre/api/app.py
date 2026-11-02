"""Read API for the dashboard, plus the paper-only action endpoints.

Write endpoints: POST /control/halt, /control/resume, /control/run-cycle and /orders
(a manual PAPER order, tagged USER or CLAUDE and checked by the risk engine). None of
them calls Manifold, and no route can change the execution mode.
"""

from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ambre import __version__
from ambre.api import schemas as sc
from ambre.config.settings import Settings, get_settings
from ambre.data_engine.sync import SYNC_KEY
from ambre.domain.models import Action, Actor, Side, StrategyName
from ambre.execution_engine.executor import ExecutionModeNotAvailable
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import DecisionRow, MarketRow
from ambre.pipeline import (
    ManualOrder,
    ManualOrderError,
    evaluation_for_simulation,
    manual_order,
    run_cycle,
)
from ambre.portfolio.service import PortfolioService

NO_SIMULATION = "no active simulation; run `ambre cycle`"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    cfg = settings or get_settings()
    holder: dict[str, sessionmaker[Session]] = {}
    if session_factory is not None:
        holder["factory"] = session_factory

    def factory() -> sessionmaker[Session]:
        if "factory" not in holder:  # lazy: importing the module never touches the DB
            holder["factory"] = make_session_factory(make_engine(cfg.database_url))
        return holder["factory"]

    def get_session() -> Iterator[Session]:
        with factory()() as session:
            yield session

    Db = Annotated[Session, Depends(get_session)]

    app = FastAPI(title="AMBRE API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def current_sim_id(session: Session) -> str | None:
        sim = repo.get_current_simulation(session)
        return sim.id if sim else None

    def decision_out(d: DecisionRow, questions: dict[str, str]) -> sc.DecisionOut:
        out = sc.DecisionOut.model_validate(d)
        out.market_question = questions.get(d.market_id)
        return out

    def questions_for(session: Session, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = session.query(MarketRow.id, MarketRow.question).filter(MarketRow.id.in_(ids))
        return {i: q for i, q in rows}

    @app.get("/health", response_model=sc.HealthOut)
    def health() -> JSONResponse:
        try:
            with factory()() as session:
                session.execute(text("SELECT 1"))
            ok = True
        except Exception:
            ok = False
        body = sc.HealthOut(status="ok" if ok else "degraded", db_ok=ok, version=__version__)
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @app.get("/system", response_model=sc.SystemOut)
    def system(session: Db) -> sc.SystemOut:
        control = repo.get_control(session)
        sync = repo.get_sync_state(session, SYNC_KEY)
        last_ok = sync.last_success_at if sync else None
        sim_id = current_sim_id(session)
        last_cycle = repo.latest_portfolio_snapshot(session, sim_id) if sim_id else None
        return sc.SystemOut(
            execution_mode=cfg.execution_mode.value,
            orders_enabled=cfg.enable_manifold_orders,
            halted=control.halted if control else False,
            halt_reason=control.reason if control else None,
            last_sync_at=last_ok,
            last_sync_error=sync.last_error if sync else None,
            data_age_seconds=(clock() - last_ok).total_seconds() if last_ok else None,
            last_cycle_at=last_cycle.at if last_cycle else None,
            decisions_total=repo.count_decisions(session, sim_id) if sim_id else 0,
            active_strategy=StrategyName.VALUE_HUNTER.value,
            risk=cfg.risk,
            survival=cfg.survival,
        )

    @app.get("/markets", response_model=sc.MarketsPage)
    def markets(
        session: Db,
        status: Literal["open", "resolved", "all"] = "open",
        q: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> sc.MarketsPage:
        rows, total = repo.list_markets_filtered(
            session, status=status, q=q, limit=limit, offset=offset
        )
        sim_id = current_sim_id(session)
        latest = (
            repo.latest_decisions_by_market(session, sim_id, [m.id for m in rows]) if sim_id else {}
        )
        items = []
        for m in rows:
            snap = repo.latest_snapshot_for_market(session, m.id)
            d = latest.get(m.id)
            items.append(
                sc.MarketRowOut(
                    id=m.id,
                    question=m.question,
                    url=m.url,
                    close_at=m.close_at,
                    is_resolved=m.is_resolved,
                    resolution=m.resolution,
                    probability=snap.probability if snap else None,
                    total_liquidity=snap.total_liquidity if snap else None,
                    unique_bettor_count=snap.unique_bettor_count if snap else None,
                    observed_at=snap.observed_at if snap else None,
                    ambre_estimate=d.estimated_probability if d else None,
                    ambre_uncertainty=d.uncertainty if d else None,
                    ambre_ev_per_mana=d.ev_per_mana if d else None,
                    last_action=d.action if d else None,
                )
            )
        return sc.MarketsPage(items=items, total=total)

    @app.get("/markets/{market_id}", response_model=sc.MarketDetailOut)
    def market_detail(market_id: str, session: Db) -> sc.MarketDetailOut:
        market = repo.get_market_detail(session, market_id)
        if market is None:
            raise HTTPException(404, f"unknown market {market_id}")
        sim_id = current_sim_id(session)
        decisions: list[DecisionRow] = []
        if sim_id:
            decisions, _ = repo.list_decisions_filtered(
                session, sim_id, market_id=market_id, limit=1000
            )
        return sc.MarketDetailOut(
            market=sc.MarketOut.model_validate(market),
            snapshots=[
                sc.SnapshotOut.model_validate(s)
                for s in repo.list_snapshots_for_market(session, market_id)
            ],
            decisions=[decision_out(d, {market_id: market.question}) for d in decisions],
        )

    @app.get("/decisions", response_model=sc.DecisionsPage)
    def decisions(
        session: Db,
        action: Action | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> sc.DecisionsPage:
        sim_id = current_sim_id(session)
        if sim_id is None:
            return sc.DecisionsPage(items=[], total=0)
        rows, total = repo.list_decisions_filtered(
            session,
            sim_id,
            action=action.value if action else None,
            limit=limit,
            offset=offset,
        )
        questions = questions_for(session, {d.market_id for d in rows})
        return sc.DecisionsPage(items=[decision_out(d, questions) for d in rows], total=total)

    def require_simulation(session: Session):  # type: ignore[no-untyped-def]
        sim = repo.get_current_simulation(session)
        if sim is None:
            raise HTTPException(404, NO_SIMULATION)
        return sim

    @app.get("/evaluation", response_model=sc.EvaluationOut)
    def evaluation(session: Db) -> sc.EvaluationOut:
        sim = require_simulation(session)
        report = evaluation_for_simulation(session, sim.id)
        return sc.EvaluationOut.model_validate(report)

    @app.get("/portfolio", response_model=sc.PortfolioOut)
    def portfolio(session: Db) -> sc.PortfolioOut:
        sim = require_simulation(session)
        svc = PortfolioService(session, sim.id)
        positions = svc.open_positions()
        prices: dict[str, float] = {}
        for pos in positions:
            snap = repo.latest_snapshot_for_market(session, pos.market_id)
            if snap is not None:
                prices[pos.market_id] = snap.probability
        valued = svc.position_values(prices, positions)
        cash = svc.cash()
        positions_value = sum(v for _, _, v in valued if v is not None)
        total_value = cash + positions_value
        exposure_known = all(v is not None for _, _, v in valued)
        questions = questions_for(session, {p.market_id for p in positions})
        items = [
            sc.PositionOut(
                market_id=pos.market_id,
                question=questions.get(pos.market_id),
                side=pos.side,
                shares=pos.shares,
                cost=pos.cost,
                opened_at=pos.opened_at,
                current_price=price,
                current_value=value,
            )
            for pos, price, value in valued
        ]
        peak = max(svc.peak_value(), total_value)
        snap_row = repo.latest_portfolio_snapshot(session, sim.id)
        return sc.PortfolioOut(
            simulation=sc.SimulationOut.model_validate(sim),
            cash=cash,
            positions_value=positions_value,
            total_value=total_value,
            exposure_known=exposure_known,
            total_value_is_lower_bound=not exposure_known,
            peak_value=peak,
            drawdown=max(0.0, 1.0 - total_value / peak) if exposure_known else None,
            survival_state=snap_row.survival_state if snap_row else None,
            survival_reasons=list(snap_row.survival_reasons) if snap_row else [],
            open_positions=items,
        )

    @app.get("/portfolio/history", response_model=list[sc.PortfolioPointOut])
    def portfolio_history(session: Db) -> list[sc.PortfolioPointOut]:
        sim = require_simulation(session)
        rows = repo.list_portfolio_snapshots(session, sim.id)  # all, ascending
        return [sc.PortfolioPointOut.model_validate(r) for r in rows]

    @app.post("/control/halt", response_model=sc.ControlOut)
    def halt(body: sc.HaltIn, session: Db) -> sc.ControlOut:
        row = repo.set_control(session, halted=True, reason=body.reason, updated_at=clock())
        session.commit()
        return sc.ControlOut.model_validate(row)

    @app.post("/control/resume", response_model=sc.ControlOut)
    def resume(body: sc.ResumeIn, session: Db) -> sc.ControlOut:
        row = repo.set_control(session, halted=False, reason=None, updated_at=clock())
        session.commit()
        return sc.ControlOut.model_validate(row)

    order_status = {
        "unknown_market": 404,
        "no_simulation": 409,
        "simulation_dead": 409,
        "ineligible": 422,
    }

    @app.post("/orders", response_model=sc.DecisionOut)
    def place_order(body: sc.OrderIn, session: Db) -> sc.DecisionOut:
        order = ManualOrder(
            market_id=body.market_id,
            side=Side(body.side),
            probability=body.probability,
            stake=body.stake,
            actor=Actor(body.actor),
            request_id=body.request_id,
            note=body.note,
        )
        try:
            decision_id = manual_order(session, cfg, clock(), order)
        except ManualOrderError as exc:
            raise HTTPException(order_status[exc.kind], exc.detail) from exc
        except ExecutionModeNotAvailable as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        row = session.get(DecisionRow, decision_id)
        assert row is not None
        return decision_out(row, questions_for(session, {row.market_id}))

    @app.post("/control/run-cycle", response_model=sc.CycleOut)
    def run_cycle_now(session: Db) -> sc.CycleOut:
        """One paper decision cycle on the data already stored (no Manifold call)."""
        try:
            report = run_cycle(session, cfg, clock())
        except ExecutionModeNotAvailable as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        data = asdict(report)
        data["survival_state"] = report.survival_state.value
        return sc.CycleOut.model_validate(data)

    return app


app = create_app()

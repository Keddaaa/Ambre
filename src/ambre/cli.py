"""`ambre` command line: init-db, sync, cycle, worker, research, evaluate, backtest, new-simulation.

Every command first prints ``mode=<MODE> orders_enabled=<bool> db=<masked url>``
on stdout, then its report as one JSON line per report. Logs go to stderr.
Secrets are never printed: the database password is masked and the Manifold
API key is never read here.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ambre.cognitive.vault import export_markdown
from ambre.config.settings import Settings
from ambre.data_engine.sync import MarketSyncService, SyncReport
from ambre.evaluation_engine.backtest import run_backtest
from ambre.execution_engine.executor import ExecutionModeNotAvailable
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.integrations.manifold.client import ManifoldClient
from ambre.intelligence.connectors import CONNECTORS, build_providers
from ambre.intelligence.coverage import coverage_report
from ambre.intelligence.planning import load_gazetteer
from ambre.intelligence.registry import load_registry
from ambre.intelligence.scheduler import JobSpec, tick
from ambre.intelligence.scheduler import status as scheduler_status
from ambre.intelligence.service import research_pass
from ambre.pipeline import CycleReport, evaluation_for_simulation, run_cycle
from ambre.portfolio.service import create_simulation

logger = logging.getLogger("ambre.cli")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_MAX_PAGES = 5

ClientFactory = Callable[[Settings], ManifoldClient]
HttpFactory = Callable[[], httpx.AsyncClient]
Clock = Callable[[], datetime]
Sleep = Callable[[float], None]


def default_client_factory(settings: Settings) -> ManifoldClient:
    return ManifoldClient(settings.manifold_base_url)


def default_http_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30.0)


def utc_now() -> datetime:
    return datetime.now(UTC)


def mask_db_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # an unparsable URL could still embed a secret
        return "<unparsable database url>"


def header_line(settings: Settings) -> str:
    orders = str(settings.enable_manifold_orders).lower()
    return (
        f"mode={settings.execution_mode.value} orders_enabled={orders} "
        f"db={mask_db_url(settings.database_url)}"
    )


def _emit(payload: Any) -> None:
    print(json.dumps(payload, default=str), flush=True)


def _cycle_dict(report: CycleReport) -> dict[str, Any]:
    return asdict(report)


# --------------------------------------------------------------------------- commands


def init_db(settings: Settings) -> dict[str, Any]:
    from alembic import command
    from alembic.config import Config

    # No ini file: migrations/env.py would otherwise reset logging with fileConfig().
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # ConfigParser interpolation: a literal '%' (URL-encoded password) must be doubled.
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    return {"migrated_to": "head"}


def sync_once(
    settings: Settings,
    factory: sessionmaker[Session],
    client_factory: ClientFactory,
    clock: Clock,
    max_pages: int,
) -> SyncReport:
    async def _run() -> SyncReport:
        client = client_factory(settings)
        try:
            return await MarketSyncService(client, factory, clock).sync(max_pages=max_pages)
        finally:
            await client.aclose()

    return asyncio.run(_run())


def cycle_once(settings: Settings, factory: sessionmaker[Session], clock: Clock) -> CycleReport:
    """One cycle in one transaction: commit on success, roll back on any exception."""
    with factory() as session:
        try:
            report = run_cycle(session, settings, clock())
            session.commit()
        except BaseException:
            session.rollback()
            raise
    return report


def worker(
    settings: Settings,
    factory: sessionmaker[Session],
    client_factory: ClientFactory,
    clock: Clock,
    sleep: Sleep,
    interval: float,
    max_pages: int,
    http_factory: HttpFactory | None = None,
) -> int:
    """Loop sync, research tick (when scheduled) then cycle. A failed sync or research tick is
    logged and the cycle runs anyway (survival turns stale data into OBSERVING). Ctrl-C stops the
    loop with exit code 0."""
    try:
        while True:
            sync_report: SyncReport | None = None
            try:
                sync_report = sync_once(settings, factory, client_factory, clock, max_pages)
                if not sync_report.completed:
                    logger.warning("sync incomplete: %s", sync_report.error)
            except Exception:
                logger.exception("sync failed; running the cycle anyway")
            research: list[dict[str, Any]] | None = None
            if settings.intelligence.schedule_research:
                try:
                    research = research_tick(settings, factory, clock, http_factory)
                except Exception:
                    logger.exception("research tick failed; the decision cycle goes on")
            cycle: dict[str, Any] | None = None
            try:
                cycle = _cycle_dict(cycle_once(settings, factory, clock))
            except ExecutionModeNotAvailable:
                raise
            except Exception:
                logger.exception("cycle failed (rolled back); retrying next interval")
            _emit(
                {
                    "sync": asdict(sync_report) if sync_report else None,
                    "research": research,
                    "cycle": cycle,
                }
            )
            sleep(interval)
    except KeyboardInterrupt:
        logger.info("worker stopped by Ctrl-C")
        return 0


def _research_in_session(
    settings: Settings,
    session: Session,
    now: datetime,
    clock: Clock,
    http_factory: HttpFactory | None,
) -> dict[str, Any]:
    """One research pass inside ``session`` (the caller commits). Read-only toward every
    provider, writes only the intelligence tables, never a decision. No HTTP client is created
    when no enabled provider has a connector."""
    registry = load_registry(settings.providers_path)
    gazetteer = load_gazetteer(settings.gazetteer_path)
    factory_ = http_factory or default_http_factory

    async def _run() -> dict[str, Any]:
        needs_http = any(c.id in CONNECTORS for c in registry.enabled())
        http = factory_() if needs_http else None
        try:
            providers = build_providers(registry, http, clock) if http is not None else {}
            report = await research_pass(
                session,
                settings,
                now,
                registry=registry,
                providers=providers,
                gazetteer=gazetteer,
                sleep=asyncio.sleep,
            )
        finally:
            if http is not None:
                await http.aclose()
        out = asdict(report)
        out["markets_without_information"] = sorted(set(out["markets_without_information"]))
        return out

    return asyncio.run(_run())


def research_once(
    settings: Settings,
    factory: sessionmaker[Session],
    clock: Clock,
    http_factory: HttpFactory | None = None,
) -> dict[str, Any]:
    """One research pass in one transaction (commit on success, roll back on any exception)."""
    with factory() as session:
        try:
            result = _research_in_session(settings, session, clock(), clock, http_factory)
            session.commit()
        except BaseException:
            session.rollback()
            raise
    return result


def research_job(settings: Settings, clock: Clock, http_factory: HttpFactory | None) -> JobSpec:
    """The scheduled research job: one pass per interval, summarised for the run journal."""

    def run(session: Session, now: datetime) -> dict[str, Any]:
        full = _research_in_session(settings, session, now, clock, http_factory)
        return {
            "calls": full["calls"],
            "new_observations": full["new_observations"],
            "new_links": full["new_links"],
            "requests": [
                {"status": r["status"], "provider": r["provider"], "markets": len(r["markets"])}
                for r in full["requests"]
            ],
            "plans": full["plans"],
            "states": full["states"],
            "queued_groups": full["queued_groups"],
            "waited_seconds": full["waited_seconds"],
            "markets_without_information": len(full["markets_without_information"]),
        }

    return JobSpec("research", settings.intelligence.research_interval_seconds, run)


def research_tick(
    settings: Settings,
    factory: sessionmaker[Session],
    clock: Clock,
    http_factory: HttpFactory | None = None,
) -> list[dict[str, Any]]:
    """Run the research job if it is due (persistent schedule, emergency stop, lease, journal)."""
    spec = research_job(settings, clock, http_factory)
    results = tick(
        factory,
        clock(),
        {spec.name: spec},
        lease_seconds=settings.intelligence.job_lease_seconds,
    )
    return [{"job": r.job, "status": r.status, "detail": r.detail} for r in results]


def scheduler_snapshot(factory: sessionmaker[Session]) -> dict[str, Any]:
    with factory() as session:
        return scheduler_status(session)


def coverage(factory: sessionmaker[Session], settings: Settings | None = None) -> dict[str, Any]:
    """Coverage of the information network over every unresolved market, with denominators."""
    with factory() as session:
        return coverage_report(session, settings)


def vault_export(factory: sessionmaker[Session], directory: Path) -> dict[str, Any]:
    """Write the Cognitive Vault as Markdown files. Read-only toward the database."""
    with factory() as session:
        written = export_markdown(session, directory)
    return {"exported": len(written), "directory": str(directory)}


def new_simulation(
    settings: Settings, factory: sessionmaker[Session], clock: Clock, name: str
) -> dict[str, Any]:
    with factory.begin() as session:
        sim_id = create_simulation(session, name, settings.initial_capital, clock())
    return {"simulation_id": sim_id, "name": name, "initial_capital": settings.initial_capital}


def evaluate(factory: sessionmaker[Session]) -> dict[str, Any] | None:
    """Score the active simulation on resolved markets; None when there is no simulation."""
    with factory() as session:
        sim = repo.get_current_simulation(session)
        if sim is None:
            return None
        return asdict(evaluation_for_simulation(session, sim.id))


def backtest(factory: sessionmaker[Session], settings: Settings | None = None) -> dict[str, Any]:
    """Walk-forward backtest of the base-rate baseline on resolved markets (read-only)."""
    cfg = (settings or Settings()).prediction
    with factory() as session:
        result = run_backtest(repo.backtest_markets(session), cfg)
    return asdict(result)


# --------------------------------------------------------------------------- entry point


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ambre", description="AMBRE paper-trading engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="apply database migrations (alembic upgrade head)")
    sync = sub.add_parser("sync", help="synchronise Manifold markets (read-only GETs)")
    sync.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    sub.add_parser("cycle", help="run one paper decision cycle")
    work = sub.add_parser("worker", help="loop sync + cycle until Ctrl-C")
    work.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    work.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    sub.add_parser("research", help="one information-seeking pass (read-only, never places a bet)")
    sub.add_parser("coverage", help="information coverage of every unresolved market")
    sub.add_parser("scheduler-status", help="scheduled jobs and their recent runs")
    export = sub.add_parser("vault-export", help="export the Cognitive Vault as Markdown files")
    export.add_argument("--dir", required=True, type=Path)
    sub.add_parser("evaluate", help="Brier, log loss and calibration on resolved markets")
    sub.add_parser("backtest", help="chronological walk-forward backtest, no look-ahead")
    new = sub.add_parser("new-simulation", help="archive the active simulation, start a new one")
    new.add_argument("--name", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = default_client_factory,
    clock: Clock = utc_now,
    sleep: Sleep = time.sleep,
    http_factory: HttpFactory | None = None,
) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = Settings()
    print(header_line(settings), flush=True)

    if args.command == "init-db":
        _emit(init_db(settings))
        return 0

    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        if args.command == "sync":
            report = sync_once(settings, factory, client_factory, clock, args.max_pages)
            _emit(asdict(report))
            return 0 if report.completed else 1
        if args.command == "cycle":
            _emit(_cycle_dict(cycle_once(settings, factory, clock)))
            return 0
        if args.command == "worker":
            return worker(
                settings,
                factory,
                client_factory,
                clock,
                sleep,
                args.interval,
                args.max_pages,
                http_factory,
            )
        if args.command == "research":
            _emit(research_once(settings, factory, clock, http_factory))
            return 0
        if args.command == "coverage":
            _emit(coverage(factory, settings))
            return 0
        if args.command == "scheduler-status":
            _emit(scheduler_snapshot(factory))
            return 0
        if args.command == "vault-export":
            _emit(vault_export(factory, args.dir))
            return 0
        if args.command == "evaluate":
            result = evaluate(factory)
            if result is None:
                logger.error("no active simulation; run `ambre cycle` first")
                return 1
            _emit(result)
            return 0
        if args.command == "backtest":
            _emit(backtest(factory, settings))
            return 0
        if args.command == "new-simulation":
            _emit(new_simulation(settings, factory, clock, args.name))
            return 0
        raise AssertionError(f"unhandled command {args.command}")
    except ExecutionModeNotAvailable as exc:
        logger.error("%s", exc)
        return 2
    except Exception:
        logger.exception("ambre %s failed", args.command)
        return 1
    finally:
        engine.dispose()

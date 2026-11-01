"""`ambre` command line: init-db, sync, cycle, worker, evaluate, new-simulation.

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

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ambre.config.settings import Settings
from ambre.data_engine.sync import MarketSyncService, SyncReport
from ambre.execution_engine.executor import ExecutionModeNotAvailable
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.integrations.manifold.client import ManifoldClient
from ambre.pipeline import CycleReport, evaluation_for_simulation, run_cycle
from ambre.portfolio.service import create_simulation

logger = logging.getLogger("ambre.cli")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_MAX_PAGES = 5

ClientFactory = Callable[[Settings], ManifoldClient]
Clock = Callable[[], datetime]
Sleep = Callable[[float], None]


def default_client_factory(settings: Settings) -> ManifoldClient:
    return ManifoldClient(settings.manifold_base_url)


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
) -> int:
    """Loop sync then cycle. A failed sync is logged and the cycle runs anyway (survival
    turns stale data into OBSERVING). Ctrl-C stops the loop with exit code 0."""
    try:
        while True:
            sync_report: SyncReport | None = None
            try:
                sync_report = sync_once(settings, factory, client_factory, clock, max_pages)
                if not sync_report.completed:
                    logger.warning("sync incomplete: %s", sync_report.error)
            except Exception:
                logger.exception("sync failed; running the cycle anyway")
            cycle: dict[str, Any] | None = None
            try:
                cycle = _cycle_dict(cycle_once(settings, factory, clock))
            except ExecutionModeNotAvailable:
                raise
            except Exception:
                logger.exception("cycle failed (rolled back); retrying next interval")
            _emit({"sync": asdict(sync_report) if sync_report else None, "cycle": cycle})
            sleep(interval)
    except KeyboardInterrupt:
        logger.info("worker stopped by Ctrl-C")
        return 0


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
    sub.add_parser("evaluate", help="Brier, log loss and calibration on resolved markets")
    new = sub.add_parser("new-simulation", help="archive the active simulation, start a new one")
    new.add_argument("--name", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = default_client_factory,
    clock: Clock = utc_now,
    sleep: Sleep = time.sleep,
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
                settings, factory, client_factory, clock, sleep, args.interval, args.max_pages
            )
        if args.command == "evaluate":
            result = evaluate(factory)
            if result is None:
                logger.error("no active simulation; run `ambre cycle` first")
                return 1
            _emit(result)
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

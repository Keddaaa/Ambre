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
from ambre.data_engine.history_fetch import fetch_histories, load_resolved_universe
from ambre.data_engine.sync import MarketSyncService, SyncReport
from ambre.evaluation_engine.backtest import run_backtest
from ambre.evaluation_engine.historical import (
    DEFAULT_HORIZONS_DAYS,
    build_observations,
    evaluate_historical,
)
from ambre.execution_engine.executor import ExecutionModeNotAvailable
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.integrations.manifold.client import ManifoldClient
from ambre.intelligence.connectors import CONNECTORS, build_providers
from ambre.intelligence.coverage import coverage_report
from ambre.intelligence.models import InfoNeed
from ambre.intelligence.news_ingest import ingest_news
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
            if settings.intelligence.schedule_research or settings.intelligence.schedule_news:
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
        needs_http = any((c.connector or c.id) in CONNECTORS for c in registry.enabled())
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


def _news_in_session(
    settings: Settings,
    session: Session,
    now: datetime,
    clock: Clock,
    http_factory: HttpFactory | None,
) -> dict[str, Any]:
    """One shared news ingestion inside ``session`` (the caller commits): every enabled feed is
    polled once, then the stored articles are associated with every open market. Read-only toward
    the providers; writes only the intelligence tables, never a decision."""
    registry = load_registry(settings.providers_path)
    gazetteer = load_gazetteer(settings.gazetteer_path)
    factory_ = http_factory or default_http_factory

    async def _run() -> dict[str, Any]:
        wanted = [c for c in registry.providers_for(InfoNeed.NEWS_FEED)]
        http = factory_() if wanted else None
        try:
            providers = build_providers(registry, http, clock) if http is not None else {}
            report = await ingest_news(
                session,
                settings,
                now,
                registry=registry,
                providers=providers,
                gazetteer=gazetteer,
                window_days=settings.intelligence.news_window_days,
                clock=clock,
            )
        finally:
            if http is not None:
                await http.aclose()
        return asdict(report)

    return asyncio.run(_run())


def news_once(
    settings: Settings,
    factory: sessionmaker[Session],
    clock: Clock,
    http_factory: HttpFactory | None = None,
) -> dict[str, Any]:
    """One news ingestion in one transaction (commit on success, roll back on any exception)."""
    with factory() as session:
        try:
            result = _news_in_session(settings, session, clock(), clock, http_factory)
            session.commit()
        except BaseException:
            session.rollback()
            raise
    return result


def news_job(settings: Settings, clock: Clock, http_factory: HttpFactory | None) -> JobSpec:
    """The scheduled news job. Separate from the research job: neither can delay or fail the
    other, since the scheduler runs each in its own transaction."""

    def run(session: Session, now: datetime) -> dict[str, Any]:
        full = _news_in_session(settings, session, now, clock, http_factory)
        return {
            "polls": [
                {k: p.get(k) for k in ("provider", "status", "items", "new", "detail")}
                for p in full["polls"]
            ],
            "new_observations": full["new_observations"],
            "new_links": full["new_links"],
            "journaled": full["journaled"],
            "articles_in_window": full["articles_in_window"],
            "markets_newly_linked": len(full["markets_newly_linked"]),
        }

    return JobSpec("news_ingest", settings.intelligence.news_interval_seconds, run)


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
    jobs: dict[str, JobSpec] = {}
    if settings.intelligence.schedule_research:
        spec = research_job(settings, clock, http_factory)
        jobs[spec.name] = spec
    if settings.intelligence.schedule_news:
        news = news_job(settings, clock, http_factory)
        jobs[news.name] = news
    results = tick(
        factory,
        clock(),
        jobs,
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


def history_fetch(
    settings: Settings,
    factory: sessionmaker[Session],
    client_factory: ClientFactory,
    clock: Clock,
    limit: int,
    max_pages: int,
) -> dict[str, Any]:
    """Fetch the bet history of resolved markets that have none (read-only GETs)."""

    async def _run() -> dict[str, Any]:
        client = client_factory(settings)
        try:
            report = await fetch_histories(client, factory, clock, limit=limit, max_pages=max_pages)
        finally:
            await client.aclose()
        return asdict(report)

    return asyncio.run(_run())


def history_universe(
    settings: Settings,
    client_factory: ClientFactory,
    factory: sessionmaker[Session],
    clock: Clock,
    since: datetime,
    stride_days: int,
    per_anchor: int,
) -> dict[str, Any]:
    """Load resolved YES/NO markets sampled across time (read-only GETs)."""

    async def _run() -> dict[str, Any]:
        client = client_factory(settings)
        try:
            report = await load_resolved_universe(
                client, factory, clock, since=since, stride_days=stride_days, per_anchor=per_anchor
            )
        finally:
            await client.aclose()
        return asdict(report)

    return asyncio.run(_run())


def history_eval(
    factory: sessionmaker[Session],
    settings: Settings | None = None,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS_DAYS,
    include_folds: bool = False,
) -> dict[str, Any]:
    """Point-in-time evaluation on resolved markets (read-only; writes nothing)."""
    cfg = (settings or Settings()).prediction
    with factory() as session:
        markets = repo.historical_markets(session)
    observations, excluded = build_observations(markets, horizons)
    report = asdict(evaluate_historical(observations, excluded, cfg))
    if not include_folds:
        for h in report["horizons"]:
            del h["folds"]
    return report


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
    sub.add_parser(
        "news-ingest", help="poll every enabled news feed once and associate it with the markets"
    )
    export = sub.add_parser("vault-export", help="export the Cognitive Vault as Markdown files")
    export.add_argument("--dir", required=True, type=Path)
    sub.add_parser("evaluate", help="Brier, log loss and calibration on resolved markets")
    sub.add_parser("backtest", help="chronological walk-forward backtest, no look-ahead")
    hist = sub.add_parser("history-fetch", help="fetch the bet history of resolved markets (GET)")
    hist.add_argument("--limit", type=int, default=100)
    hist.add_argument("--max-pages", type=int, default=30)
    uni = sub.add_parser("history-universe", help="load resolved markets sampled across time (GET)")
    uni.add_argument("--since", default="2023-01-01", help="oldest creation date, YYYY-MM-DD")
    uni.add_argument("--stride-days", type=int, default=60)
    uni.add_argument("--per-anchor", type=int, default=25)
    heval = sub.add_parser("history-eval", help="point-in-time evaluation on resolved markets")
    heval.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS_DAYS))
    heval.add_argument("--folds", action="store_true", help="include every observation")
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
        if args.command == "news-ingest":
            _emit(news_once(settings, factory, clock, http_factory))
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
        if args.command == "history-fetch":
            _emit(
                history_fetch(settings, factory, client_factory, clock, args.limit, args.max_pages)
            )
            return 0
        if args.command == "history-universe":
            since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
            _emit(
                history_universe(
                    settings,
                    client_factory,
                    factory,
                    clock,
                    since,
                    args.stride_days,
                    args.per_anchor,
                )
            )
            return 0
        if args.command == "history-eval":
            horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
            _emit(history_eval(factory, settings, horizons, args.folds))
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

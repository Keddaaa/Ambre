"""One worker per database, and a written record of every run and every interruption.

A worker must hold the database's lease to sync and decide. The lease is renewed after every
iteration and expires ``ttl`` seconds after the last renewal, so a worker that dies (crash, power
cut, ``kill -9``) frees it on its own; a clean stop releases it at once. A second worker on the same
database waits for the lease, or gives up, but never runs in parallel.

Every run is journaled (``worker_runs``): when it started, its last heartbeat, how it stopped. A run
found still open when a new worker starts ended without a clean stop: it is closed as
*interrupted* at its last heartbeat, and the new run records the downtime since then.
"""

import logging
import os
import signal
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ambre.infrastructure.orm import WorkerLeaseRow, WorkerRunRow

logger = logging.getLogger(__name__)

LEASE_NAME = "worker"
ONE_SHOT_TTL_SECONDS = 900.0
CLEAN_STOP = "stopped (signal or Ctrl-C)"
INTERRUPTED = "interrupted: the process ended without a clean stop"


def lease_ttl_seconds(interval: float) -> float:
    """Long enough to cover one slow iteration and the sleep after it."""
    return 2.0 * interval + 600.0


def lease_free_at(row: WorkerLeaseRow | None, now: datetime) -> bool:
    return row is None or row.expires_at <= now


def acquire(
    factory: sessionmaker[Session], holder: str, now: datetime, ttl: float, name: str = LEASE_NAME
) -> bool:
    """Take the lease if it is free (or already ours). Compare-and-set: never two holders."""
    expires = now + timedelta(seconds=ttl)
    with factory() as s:
        row = s.get(WorkerLeaseRow, name)
        if row is None:
            s.add(
                WorkerLeaseRow(
                    name=name, holder=holder, acquired_at=now, heartbeat_at=now, expires_at=expires
                )
            )
            try:
                s.commit()
            except IntegrityError:  # another worker created it first
                s.rollback()
                return False
            return True
        if not (row.holder == holder or lease_free_at(row, now)):
            return False
        result = s.execute(
            update(WorkerLeaseRow)
            .where(
                WorkerLeaseRow.name == name,
                WorkerLeaseRow.holder == row.holder,
                WorkerLeaseRow.expires_at == row.expires_at,
            )
            .values(holder=holder, acquired_at=now, heartbeat_at=now, expires_at=expires)
        )
        s.commit()
        return bool(getattr(result, "rowcount", 0) == 1)


def renew(
    factory: sessionmaker[Session], holder: str, now: datetime, ttl: float, name: str = LEASE_NAME
) -> bool:
    """Extend our lease; False if it is no longer ours (another worker took it after expiry)."""
    with factory() as s:
        result = s.execute(
            update(WorkerLeaseRow)
            .where(WorkerLeaseRow.name == name, WorkerLeaseRow.holder == holder)
            .values(heartbeat_at=now, expires_at=now + timedelta(seconds=ttl))
        )
        s.commit()
        return bool(getattr(result, "rowcount", 0) == 1)


def release(factory: sessionmaker[Session], holder: str, name: str = LEASE_NAME) -> None:
    with factory() as s:
        row = s.get(WorkerLeaseRow, name)
        if row is not None and row.holder == holder:
            s.delete(row)
            s.commit()


class LeaseBusy(Exception):
    """A worker or another one-shot cycle is running on this database."""


@contextmanager
def exclusive(
    factory: sessionmaker[Session], now: datetime, ttl: float = ONE_SHOT_TTL_SECONDS
) -> Iterator[None]:
    """Hold the worker's lease for a single cycle started outside the worker loop."""
    holder = f"one-shot:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    if not acquire(factory, holder, now, ttl):
        raise LeaseBusy("a cycle is already running on this database")
    try:
        yield
    finally:
        release(factory, holder)


@dataclass(frozen=True)
class RunStart:
    run_id: int
    #: seconds since the previous run's last sign of life; None for the very first run
    gap_before_seconds: float | None
    #: how many earlier runs were found open and closed as interrupted
    interrupted: int


def start_run(
    factory: sessionmaker[Session], holder: str, now: datetime, name: str = LEASE_NAME
) -> RunStart:
    with factory() as s:
        open_runs = list(
            s.scalars(
                select(WorkerRunRow).where(
                    WorkerRunRow.name == name, WorkerRunRow.stopped_at.is_(None)
                )
            )
        )
        for r in open_runs:
            r.stopped_at = r.last_heartbeat_at
            r.stop_reason = INTERRUPTED
        s.flush()
        last = s.scalars(
            select(WorkerRunRow)
            .where(WorkerRunRow.name == name)
            .order_by(WorkerRunRow.id.desc())
            .limit(1)
        ).first()
        gap = None
        if last is not None:
            seen = last.stopped_at or last.last_heartbeat_at
            gap = (now - seen).total_seconds()
        run = WorkerRunRow(
            name=name,
            holder=holder,
            started_at=now,
            last_heartbeat_at=now,
            iterations=0,
            gap_before_seconds=gap,
        )
        s.add(run)
        s.commit()
        return RunStart(run.id, gap, len(open_runs))


def heartbeat_run(factory: sessionmaker[Session], run_id: int, now: datetime) -> None:
    with factory() as s:
        run = s.get(WorkerRunRow, run_id)
        if run is not None:
            run.last_heartbeat_at = now
            run.iterations += 1
            s.commit()


def stop_run(factory: sessionmaker[Session], run_id: int, now: datetime, reason: str) -> None:
    with factory() as s:
        run = s.get(WorkerRunRow, run_id)
        if run is not None and run.stopped_at is None:
            run.stopped_at = now
            run.stop_reason = reason
            s.commit()


def status(session: Session, now: datetime, runs_limit: int = 20) -> dict[str, Any]:
    row = session.get(WorkerLeaseRow, LEASE_NAME)
    runs = list(
        session.scalars(select(WorkerRunRow).order_by(WorkerRunRow.id.desc()).limit(runs_limit))
    )
    return {
        "lease": None
        if row is None
        else {
            "holder": row.holder,
            "acquired_at": row.acquired_at,
            "heartbeat_at": row.heartbeat_at,
            "expires_at": row.expires_at,
            "heartbeat_age_seconds": (now - row.heartbeat_at).total_seconds(),
            "expired": lease_free_at(row, now),
        },
        "runs": [
            {
                "id": r.id,
                "holder": r.holder,
                "started_at": r.started_at,
                "last_heartbeat_at": r.last_heartbeat_at,
                "stopped_at": r.stopped_at,
                "stop_reason": r.stop_reason,
                "iterations": r.iterations,
                "gap_before_seconds": r.gap_before_seconds,
            }
            for r in runs
        ],
        "interruptions": sum(1 for r in runs if r.stop_reason == INTERRUPTED),
    }


def healthy(
    session: Session, now: datetime, max_age_seconds: float, holder_prefix: str | None = None
) -> bool:
    """A fresh heartbeat; with ``holder_prefix``, also held by a worker of this host or container.

    The prefix matters after a crash: the dead worker's lease looks fresh until it expires, while
    the new worker is still waiting for it and is doing nothing.
    """
    row = session.get(WorkerLeaseRow, LEASE_NAME)
    if row is None or (now - row.heartbeat_at).total_seconds() > max_age_seconds:
        return False
    return holder_prefix is None or row.holder.startswith(holder_prefix)


# --------------------------------------------------------------------------- clean stops

SigtermHandler = Callable[[int, Any], Any] | int | None


def _raise_interrupt(_signum: int, _frame: Any) -> None:
    # One stop is enough. A supervisor and ``uv run`` can each send SIGTERM: a second one raised
    # during the cleanup left the run unjournaled and the lease held for its whole TTL.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt  # handled by the worker loop exactly like Ctrl-C


def install_sigterm_handler() -> SigtermHandler:
    """``docker stop`` sends SIGTERM: turn it into the same clean stop as Ctrl-C."""
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    return previous


def restore_sigterm_handler(previous: SigtermHandler) -> None:
    signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)

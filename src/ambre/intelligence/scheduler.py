"""Persistent, idempotent job scheduler for the research pass.

Guarantees, each covered by a test:

- Jobs and every scheduling decision live in the database, so a restart loses nothing.
- ``next_run_at`` only moves when the job really ran or failed. A run refused by the emergency
  stop, by a lease held by another runner, or by a duplicate slot leaves the job due: an
  opportunity is never consumed by something that did not happen.
- A lease keeps two runners from executing the same job at once; an expired lease is a crashed
  run and is recovered (its RUNNING row is marked ABANDONED).
- (job, slot) is unique with a one-minute slot, so a job can never execute twice in the same
  minute whatever the runners do.
- A failure is journaled, rolls back the job's own writes, and backs off (never sooner than the
  normal rhythm, capped), without stopping the scheduler.

Provider budgets are not handled here: the research pass applies them (Budget Controller).
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ambre.infrastructure import repositories as repo
from ambre.intelligence.budget import backoff_seconds
from ambre.intelligence.orm import IntelJobRow, IntelJobRunRow

logger = logging.getLogger(__name__)

SLOT_SECONDS = 60
DEFAULT_LEASE_SECONDS = 900
BACKOFF_CAP_SECONDS = 3600


@dataclass(frozen=True)
class JobSpec:
    name: str
    interval_seconds: int
    run: Callable[[Session, datetime], dict[str, Any]]


@dataclass(frozen=True)
class RunResult:
    job: str
    status: str
    detail: dict[str, Any]


def control_halted(session: Session) -> bool:
    """True while the emergency stop is armed."""
    control = repo.get_control(session)
    return control is not None and control.halted


def _slot(now: datetime) -> int:
    return int(now.timestamp() // SLOT_SECONDS)


def _journal(
    factory: sessionmaker[Session], name: str, status: str, now: datetime, detail: dict[str, Any]
) -> RunResult:
    with factory() as s:
        s.add(
            IntelJobRunRow(
                job_name=name,
                slot=None,
                status=status,
                started_at=now,
                finished_at=now,
                detail=detail,
            )
        )
        s.commit()
    return RunResult(name, status, detail)


def _ensure_job(factory: sessionmaker[Session], spec: JobSpec, now: datetime) -> None:
    with factory() as s:
        row = s.get(IntelJobRow, spec.name)
        if row is None:
            s.add(
                IntelJobRow(
                    name=spec.name,
                    interval_seconds=spec.interval_seconds,
                    enabled=True,
                    next_run_at=now,
                    consecutive_failures=0,
                    updated_at=now,
                )
            )
        elif row.interval_seconds != spec.interval_seconds:
            row.interval_seconds = (
                spec.interval_seconds
            )  # the rhythm changes, the due time does not
            row.updated_at = now
        s.commit()


def _claim(
    factory: sessionmaker[Session], name: str, now: datetime, lease_seconds: int
) -> tuple[bool, bool]:
    """Take the lease. Returns (claimed, recovered); recovered = a previous lease had expired."""
    with factory() as s:
        row = s.get(IntelJobRow, name)
        assert row is not None
        previous = row.lease_until
        result = cast(
            "CursorResult[Any]",
            s.execute(
                update(IntelJobRow)
                .where(
                    IntelJobRow.name == name,
                    or_(IntelJobRow.lease_until.is_(None), IntelJobRow.lease_until <= now),
                )
                .values(lease_until=now + timedelta(seconds=lease_seconds))
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            s.rollback()
            return False, False
        recovered = previous is not None
        if recovered:
            for stale in s.scalars(
                select(IntelJobRunRow).where(
                    IntelJobRunRow.job_name == name, IntelJobRunRow.status == "RUNNING"
                )
            ):
                stale.status = "ABANDONED"
                stale.finished_at = now
                stale.detail = {**stale.detail, "reason": "lease expired: the run never finished"}
        s.commit()
        return True, recovered


def _release(
    factory: sessionmaker[Session], name: str, *, next_run_at: datetime | None, now: datetime
) -> None:
    with factory() as s:
        row = s.get(IntelJobRow, name)
        assert row is not None
        row.lease_until = None
        if next_run_at is not None:
            row.next_run_at = next_run_at
        row.updated_at = now
        s.commit()


def _run_job(
    factory: sessionmaker[Session], spec: JobSpec, now: datetime
) -> tuple[bool, dict[str, Any]]:
    """Execute in its own transaction: committed on success, rolled back on any failure."""
    with factory() as session:
        try:
            detail = spec.run(session, now)
            session.commit()
            return True, detail
        except Exception as exc:
            session.rollback()
            logger.exception("job %s failed", spec.name)
            return False, {"error": f"{type(exc).__name__}: {exc}"}


def tick(
    factory: sessionmaker[Session],
    now: datetime,
    jobs: Mapping[str, JobSpec],
    *,
    halted: Callable[[Session], bool] = control_halted,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> list[RunResult]:
    """Run every job that is due at ``now``. Returns what happened (nothing for jobs not due)."""
    results: list[RunResult] = []
    for spec in jobs.values():
        _ensure_job(factory, spec, now)
        with factory() as s:
            row = s.get(IntelJobRow, spec.name)
            assert row is not None
            if not row.enabled or row.next_run_at > now:
                continue
            is_halted = halted(s)
        if is_halted:
            results.append(
                _journal(factory, spec.name, "SKIPPED_HALTED", now, {"reason": "emergency stop"})
            )
            continue
        claimed, recovered = _claim(factory, spec.name, now, lease_seconds)
        if not claimed:
            results.append(
                _journal(factory, spec.name, "SKIPPED_LEASED", now, {"reason": "already running"})
            )
            continue
        slot = _slot(now)
        try:
            with factory() as s:
                s.add(
                    IntelJobRunRow(
                        job_name=spec.name, slot=slot, status="RUNNING", started_at=now, detail={}
                    )
                )
                s.commit()
        except IntegrityError:
            _release(
                factory,
                spec.name,
                next_run_at=now + timedelta(seconds=spec.interval_seconds),
                now=now,
            )
            results.append(
                _journal(factory, spec.name, "SKIPPED_DUPLICATE_SLOT", now, {"slot": slot})
            )
            continue

        ok, detail = _run_job(factory, spec, now)
        if recovered:
            detail = {**detail, "recovered_after_crash": True}
        with factory() as s:
            run = s.scalars(
                select(IntelJobRunRow).where(
                    IntelJobRunRow.job_name == spec.name, IntelJobRunRow.slot == slot
                )
            ).one()
            run.status = "OK" if ok else "FAILED"
            run.finished_at = now
            run.detail = detail
            job = s.get(IntelJobRow, spec.name)
            assert job is not None
            job.last_run_at = now
            job.last_status = run.status
            job.updated_at = now
            job.lease_until = None
            if ok:
                job.consecutive_failures = 0
                job.last_error = None
                job.next_run_at = now + timedelta(seconds=spec.interval_seconds)
            else:
                job.consecutive_failures += 1
                job.last_error = str(detail.get("error"))
                wait = backoff_seconds(
                    job.consecutive_failures,
                    base=float(spec.interval_seconds),
                    cap=float(max(BACKOFF_CAP_SECONDS, spec.interval_seconds)),
                )
                job.next_run_at = now + timedelta(seconds=wait)
            s.commit()
        results.append(RunResult(spec.name, "OK" if ok else "FAILED", detail))
    return results


def request_run(session: Session, name: str, now: datetime, *, reason: str) -> bool:
    """Make a job due now because of an event. Refused for an unknown or disabled job and while
    it is running; the caller commits."""
    row = session.get(IntelJobRow, name)
    if row is None or not row.enabled:
        return False
    if row.lease_until is not None and row.lease_until > now:
        return False
    row.next_run_at = now
    row.updated_at = now
    session.add(
        IntelJobRunRow(
            job_name=name,
            slot=None,
            status="TRIGGERED",
            started_at=now,
            finished_at=now,
            detail={"reason": reason},
        )
    )
    return True


def status(session: Session, *, runs_limit: int = 20) -> dict[str, Any]:
    jobs = session.scalars(select(IntelJobRow).order_by(IntelJobRow.name))
    runs = session.scalars(
        select(IntelJobRunRow).order_by(IntelJobRunRow.id.desc()).limit(runs_limit)
    )
    return {
        "jobs": [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "enabled": j.enabled,
                "next_run_at": j.next_run_at.isoformat(),
                "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
                "last_status": j.last_status,
                "last_error": j.last_error,
                "consecutive_failures": j.consecutive_failures,
                "leased": j.lease_until is not None,
            }
            for j in jobs
        ],
        "runs": [
            {
                "job": r.job_name,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "slot": r.slot,
                "detail": r.detail,
            }
            for r in runs
        ],
    }

"""Persistent, idempotent job scheduler: halted runs do not consume opportunities, crashed runs
are recovered, a time slot can never run twice. SQLite on a file (two sessions), no Internet."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base
from ambre.intelligence import orm as intel_orm
from ambre.intelligence.scheduler import JobSpec, request_run, status, tick

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
INTERVAL = 600


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


class Probe:
    def __init__(self) -> None:
        self.runs: list[datetime] = []
        self.fail = False

    def spec(self, interval: int = INTERVAL) -> dict[str, JobSpec]:
        def run(session: Session, now: datetime) -> dict[str, Any]:
            self.runs.append(now)
            if self.fail:
                raise RuntimeError("provider exploded")
            return {"observations": 3}

        return {"research": JobSpec("research", interval, run)}


def never_halted(session: Session) -> bool:
    return False


def count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def job(factory: sessionmaker[Session]) -> intel_orm.IntelJobRow:
    with factory() as s:
        row = s.get(intel_orm.IntelJobRow, "research")
        assert row is not None
        s.expunge(row)
        return row


def runs_of(factory: sessionmaker[Session]) -> list[tuple[str, int | None]]:
    with factory() as s:
        rows = s.scalars(select(intel_orm.IntelJobRunRow).order_by(intel_orm.IntelJobRunRow.id))
        return [(r.status, r.slot) for r in rows]


def test_first_tick_runs_then_nothing_until_the_interval_passes(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    first = tick(factory, T0, p.spec(), halted=never_halted)
    assert [r.status for r in first] == ["OK"] and p.runs == [T0]
    assert tick(factory, T0 + timedelta(seconds=INTERVAL - 1), p.spec(), halted=never_halted) == []
    again = tick(factory, T0 + timedelta(seconds=INTERVAL), p.spec(), halted=never_halted)
    assert [r.status for r in again] == ["OK"] and len(p.runs) == 2
    assert job(factory).next_run_at == T0 + timedelta(seconds=2 * INTERVAL)
    slots = [slot for _, slot in runs_of(factory)]
    assert len(set(slots)) == 2 and None not in slots


def test_the_result_of_each_run_is_journaled(factory: sessionmaker[Session]) -> None:
    tick(factory, T0, Probe().spec(), halted=never_halted)
    with factory() as s:
        run = s.scalars(select(intel_orm.IntelJobRunRow)).one()
        assert run.status == "OK" and run.detail == {"observations": 3}
        assert run.started_at == T0 and run.finished_at is not None


def test_a_disabled_job_never_runs(factory: sessionmaker[Session]) -> None:
    p = Probe()
    tick(factory, T0, p.spec(), halted=never_halted)
    with factory() as s:
        s.get(intel_orm.IntelJobRow, "research").enabled = False  # type: ignore[union-attr]
        s.commit()
    assert tick(factory, T0 + timedelta(days=1), p.spec(), halted=never_halted) == []
    assert len(p.runs) == 1


def test_an_emergency_stop_skips_the_run_without_consuming_the_opportunity(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    skipped = tick(factory, T0, p.spec(), halted=lambda s: True)
    assert [r.status for r in skipped] == ["SKIPPED_HALTED"] and p.runs == []
    row = job(factory)
    assert row.next_run_at == T0 and row.last_run_at is None  # still due, nothing "consumed"
    assert runs_of(factory) == [
        ("SKIPPED_HALTED", None)
    ]  # the refusal is journaled, without a slot
    resumed = tick(factory, T0 + timedelta(seconds=30), p.spec(), halted=never_halted)
    assert [r.status for r in resumed] == ["OK"] and len(p.runs) == 1  # runs at once after resume


def test_a_failure_is_journaled_and_backs_off_without_stopping_the_scheduler(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    p.fail = True
    res = tick(factory, T0, p.spec(), halted=never_halted)
    assert [r.status for r in res] == ["FAILED"]
    row = job(factory)
    assert row.consecutive_failures == 1 and row.last_status == "FAILED"
    assert "provider exploded" in (row.last_error or "")
    first_wait = (row.next_run_at - T0).total_seconds()
    assert first_wait >= INTERVAL  # never earlier than the normal rhythm
    t = row.next_run_at
    tick(factory, t, p.spec(), halted=never_halted)
    second_wait = (job(factory).next_run_at - t).total_seconds()
    assert second_wait > first_wait  # backoff grows
    for _ in range(12):
        t = job(factory).next_run_at
        tick(factory, t, p.spec(), halted=never_halted)
    assert (job(factory).next_run_at - t).total_seconds() <= 3600  # and is capped
    p.fail = False
    t = job(factory).next_run_at
    tick(factory, t, p.spec(), halted=never_halted)
    healed = job(factory)
    assert (
        healed.consecutive_failures == 0
        and healed.last_status == "OK"
        and healed.last_error is None
    )
    assert (healed.next_run_at - t).total_seconds() == INTERVAL


def test_a_failing_job_leaves_none_of_its_own_writes_behind(factory: sessionmaker[Session]) -> None:
    from ambre.infrastructure.orm import ControlStateRow

    def run(session: Session, now: datetime) -> dict[str, Any]:
        session.add(ControlStateRow(key="leak", halted=True, reason="x", updated_at=now))
        raise RuntimeError("late failure")

    tick(factory, T0, {"research": JobSpec("research", INTERVAL, run)}, halted=never_halted)
    with factory() as s:
        assert s.get(ControlStateRow, "leak") is None  # rolled back
    assert runs_of(factory)[0][0] == "FAILED"  # but the journal kept the failure


def test_a_second_runner_cannot_execute_a_job_that_is_already_running(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    inner: list[str] = []

    def run(session: Session, now: datetime) -> dict[str, Any]:
        p.runs.append(now)
        # while this run holds the lease, another runner ticks at the same moment
        inner.extend(
            r.status
            for r in tick(
                factory, now, {"research": JobSpec("research", INTERVAL, run)}, halted=never_halted
            )
        )
        return {}

    tick(factory, T0, {"research": JobSpec("research", INTERVAL, run)}, halted=never_halted)
    assert p.runs == [T0]  # executed once
    assert inner == ["SKIPPED_LEASED"]
    assert ("SKIPPED_LEASED", None) in runs_of(factory)


def test_a_crashed_run_is_recovered_after_its_lease_expires(factory: sessionmaker[Session]) -> None:
    p = Probe()
    tick(factory, T0, p.spec(), halted=never_halted)
    t = T0 + timedelta(seconds=INTERVAL)
    with factory() as s:  # simulate a process that died mid-run: lease taken, run left RUNNING
        j = s.get(intel_orm.IntelJobRow, "research")
        assert j is not None
        j.lease_until = t + timedelta(seconds=50)
        s.add(
            intel_orm.IntelJobRunRow(
                job_name="research", slot=999999, status="RUNNING", started_at=t, detail={}
            )
        )
        s.commit()
    during = tick(factory, t + timedelta(seconds=10), p.spec(), halted=never_halted)
    assert [r.status for r in during] == ["SKIPPED_LEASED"]  # the lease still protects it
    after = tick(factory, t + timedelta(seconds=120), p.spec(), halted=never_halted)
    assert [r.status for r in after] == ["OK"] and len(p.runs) == 2
    statuses = [st for st, _ in runs_of(factory)]
    assert "ABANDONED" in statuses and "RUNNING" not in statuses
    assert job(factory).lease_until is None


def test_a_time_slot_never_runs_twice_even_if_the_job_is_made_due_again(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    tick(factory, T0, p.spec(), halted=never_halted)
    with factory() as s:
        s.get(intel_orm.IntelJobRow, "research").next_run_at = T0  # type: ignore[union-attr]
        s.commit()
    again = tick(factory, T0 + timedelta(seconds=5), p.spec(), halted=never_halted)
    assert [r.status for r in again] == ["SKIPPED_DUPLICATE_SLOT"] and len(p.runs) == 1
    assert job(factory).next_run_at > T0  # and it is moved on so it does not spin


def test_an_event_can_make_a_job_due_now_but_never_while_it_is_running(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    tick(factory, T0, p.spec(), halted=never_halted)
    t = T0 + timedelta(seconds=60)
    assert tick(factory, t, p.spec(), halted=never_halted) == []
    with factory() as s:
        assert request_run(s, "research", t, reason="new markets seen") is True
        s.commit()
    run = tick(factory, t, p.spec(), halted=never_halted)
    assert [r.status for r in run] == ["OK"]
    with factory() as s:
        assert request_run(s, "nope", t, reason="x") is False  # unknown job
        row = s.get(intel_orm.IntelJobRow, "research")
        assert row is not None
        row.lease_until = t + timedelta(seconds=100)
        assert request_run(s, "research", t, reason="while running") is False
    kinds = [st for st, _ in runs_of(factory)]
    assert "TRIGGERED" in kinds


def test_changing_the_interval_in_the_spec_updates_the_job_without_running_it_early(
    factory: sessionmaker[Session],
) -> None:
    p = Probe()
    tick(factory, T0, p.spec(600), halted=never_halted)
    assert tick(factory, T0 + timedelta(seconds=100), p.spec(1800), halted=never_halted) == []
    assert job(factory).interval_seconds == 1800
    assert len(p.runs) == 1


def test_status_lists_jobs_and_recent_runs(factory: sessionmaker[Session]) -> None:
    p = Probe()
    tick(factory, T0, p.spec(), halted=never_halted)
    tick(factory, T0 + timedelta(seconds=INTERVAL), p.spec(), halted=lambda s: True)
    with factory() as s:
        snap = status(s, runs_limit=10)
    assert snap["jobs"][0]["name"] == "research" and snap["jobs"][0]["last_status"] == "OK"
    assert [r["status"] for r in snap["runs"]] == ["SKIPPED_HALTED", "OK"]  # newest first

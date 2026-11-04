"""One worker per database, clean stops, and interruptions written down (offline, fixed clock)."""

import json
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fakes import FakeManifoldClient
from sqlalchemy import select
from test_intel_worker import earthquake_market

from ambre import cli
from ambre.infrastructure import worker_lease as lease
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import WorkerLeaseRow, WorkerRunRow

T = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'w.db'}"
    for key in list(os.environ):
        if key.startswith(("AMBRE_", "MANIFOLD_", "EXECUTION_", "ENABLE_", "DATABASE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)
    quiet = tmp_path / "quiet.yaml"  # the lease is about the loop, not about research or news
    quiet.write_text("intelligence:\n  schedule_research: false\n  schedule_news: false\n")
    monkeypatch.setenv("AMBRE_CONFIG", str(quiet))
    assert cli.main(["init-db"]) == 0
    return url


class Fake:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _settings: Any) -> FakeManifoldClient:
        self.calls += 1
        return FakeManifoldClient([earthquake_market()])


def run(
    fake: Fake, *, at: datetime = T, iterations: int = 1, extra: list[str] | None = None
) -> int:
    count = {"n": 0}

    def sleep(_: float) -> None:
        count["n"] += 1
        if count["n"] >= iterations:
            raise KeyboardInterrupt

    return cli.main(
        ["worker", "--interval", "300", *(extra or [])],
        client_factory=fake,  # type: ignore[arg-type]
        clock=lambda: at,
        sleep=sleep,
    )


def rows(db_url: str) -> tuple[list[WorkerLeaseRow], list[WorkerRunRow]]:
    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            return (
                list(s.scalars(select(WorkerLeaseRow))),
                list(s.scalars(select(WorkerRunRow).order_by(WorkerRunRow.id))),
            )
    finally:
        engine.dispose()


def hold_lease(db_url: str, holder: str, expires_at: datetime, *, open_run: bool = False) -> None:
    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            s.add(
                WorkerLeaseRow(
                    name=lease.LEASE_NAME,
                    holder=holder,
                    acquired_at=expires_at - timedelta(hours=1),
                    heartbeat_at=expires_at - timedelta(minutes=20),
                    expires_at=expires_at,
                )
            )
            if open_run:
                s.add(
                    WorkerRunRow(
                        name=lease.LEASE_NAME,
                        holder=holder,
                        started_at=expires_at - timedelta(hours=1),
                        last_heartbeat_at=expires_at - timedelta(minutes=20),
                        iterations=3,
                    )
                )
            s.commit()
    finally:
        engine.dispose()


# ------------------------------------------------------------------ one worker at a time


def test_a_clean_stop_is_journaled_and_releases_the_lease(db_url: str) -> None:
    fake = Fake()
    assert run(fake) == 0 and fake.calls == 1
    leases, runs = rows(db_url)
    assert leases == []  # released on a clean stop: the next worker starts at once
    (r,) = runs
    assert r.stop_reason == "stopped (signal or Ctrl-C)" and r.stopped_at == T
    assert r.iterations == 1 and r.gap_before_seconds is None  # nothing ran before it


def test_a_second_worker_never_runs_while_the_lease_is_held(db_url: str) -> None:
    hold_lease(db_url, "other-host:42", T + timedelta(minutes=10))
    fake = Fake()
    assert run(fake, extra=["--lease-wait-seconds", "0"]) == 3
    assert fake.calls == 0  # it synced nothing and decided nothing
    leases, runs = rows(db_url)
    assert [x.holder for x in leases] == ["other-host:42"] and runs == []


def test_a_waiting_worker_takes_over_once_the_lease_expires() -> None:
    now = T
    assert lease.lease_free_at(None, now) is True
    expired = WorkerLeaseRow(
        name="worker", holder="x", acquired_at=now, heartbeat_at=now, expires_at=now
    )
    assert lease.lease_free_at(expired, now) is True
    held = WorkerLeaseRow(
        name="worker",
        holder="x",
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=1),
    )
    assert lease.lease_free_at(held, now) is False


def test_a_crash_is_detected_on_restart_and_the_gap_is_recorded(db_url: str) -> None:
    # a worker died 2 hours ago without stopping cleanly: its lease expired, its run is open
    hold_lease(db_url, "dead-host:7", T - timedelta(hours=2), open_run=True)
    fake = Fake()
    assert run(fake) == 0 and fake.calls == 1
    _, runs = rows(db_url)
    dead, new = runs
    assert dead.stopped_at == dead.last_heartbeat_at
    assert dead.stop_reason is not None and dead.stop_reason.startswith("interrupted")
    # last heartbeat was 20 minutes before the lease expiry, itself 2 hours before now
    assert new.gap_before_seconds == pytest.approx(timedelta(hours=2, minutes=20).total_seconds())


def test_a_restart_after_a_clean_stop_records_the_downtime(db_url: str) -> None:
    fake = Fake()
    assert run(fake, at=T) == 0
    assert run(fake, at=T + timedelta(minutes=30)) == 0
    _, runs = rows(db_url)
    assert [r.stop_reason for r in runs] == ["stopped (signal or Ctrl-C)"] * 2
    assert runs[1].gap_before_seconds == pytest.approx(1800.0)


def test_sigterm_becomes_a_clean_stop() -> None:
    previous = lease.install_sigterm_handler()
    try:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)  # type: ignore[operator]
    finally:
        lease.restore_sigterm_handler(previous)


def test_a_second_sigterm_cannot_interrupt_the_cleanup() -> None:
    """Seen on 2026-11-04: two SIGTERMs, the second inside ``stop_run``, and a lease left held."""
    previous = lease.install_sigterm_handler()
    try:
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)  # ignored: nothing is raised during the cleanup
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    finally:
        lease.restore_sigterm_handler(previous)


def test_a_one_shot_cycle_is_refused_while_the_lease_is_held(db_url: str) -> None:
    factory = make_session_factory(make_engine(db_url))
    assert cli.main(["init-db"]) == 0
    assert lease.acquire(factory, "host:1", datetime.now(UTC), 600.0)
    assert cli.main(["cycle"]) == 3
    with factory() as s:
        assert s.scalar(select(WorkerLeaseRow.holder)) == "host:1"


# ------------------------------------------------------------------ health


def test_status_is_healthy_only_with_a_fresh_heartbeat(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    hold_lease(db_url, "host:1", T + timedelta(minutes=10))
    capsys.readouterr()
    # the heartbeat in hold_lease is 20 minutes old relative to the lease expiry, i.e. T - 10 min
    assert cli.main(["worker-status", "--check", "--max-age", "1200"], clock=lambda: T) == 0
    assert cli.main(["worker-status", "--check", "--max-age", "300"], clock=lambda: T) == 1
    capsys.readouterr()
    assert cli.main(["worker-status"], clock=lambda: T) == 0
    out = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert out["lease"]["holder"] == "host:1"
    assert out["lease"]["heartbeat_age_seconds"] == pytest.approx(600.0)


def test_status_without_any_worker_is_unhealthy(db_url: str) -> None:
    assert cli.main(["worker-status", "--check"], clock=lambda: T) == 1


# ------------------------------------------------------------------ the Compose service


def compose(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    return data


def test_the_forward_service_is_paper_only_isolated_persistent_and_restarting() -> None:
    doc = compose("docker-compose.forward.yml")
    assert doc["name"] == "ambre-forward"  # its own Compose project, never the main stack
    assert set(doc["services"]) == {"forward-worker"}
    fw = doc["services"]["forward-worker"]
    env = fw["environment"]
    assert env["EXECUTION_MODE"] == "PAPER" and env["ENABLE_MANIFOLD_ORDERS"] == "false"
    assert env["AMBRE_CONFIG"] == "/app/configs/forward_test.yaml"
    assert env["DATABASE_URL"] == "sqlite:////app/var/forward_test.db"
    assert str(env["MAX_MARKETS_PER_CYCLE"]) == "5000"
    assert "MANIFOLD_API_KEY" not in env and "env_file" not in fw  # no key can reach it
    assert fw["restart"] == "unless-stopped"
    assert "ambre_forward_data:/app/var" in fw["volumes"]
    assert "ambre_forward_data" in doc["volumes"]
    assert "depends_on" not in fw  # no PostgreSQL, no main database
    assert "deploy" not in fw  # never scaled: one forward worker
    assert "exec ambre worker" in fw["command"][-1]  # the worker itself receives SIGTERM
    assert fw["init"] is True  # init relays SIGTERM and exits when the worker dies: restarts
    assert fw["healthcheck"]["test"][:3] == ["CMD", "ambre", "worker-status"]
    assert "--mine" in fw["healthcheck"]["test"]  # a dead worker's lease is not our health


def test_the_main_stack_is_untouched_and_stays_in_paper_mode() -> None:
    services = compose("docker-compose.yml")["services"]
    assert "forward-worker" not in services
    main = services["worker"]["environment"]
    assert main["EXECUTION_MODE"] == "PAPER" and main["ENABLE_MANIFOLD_ORDERS"] == "false"


def test_a_fresh_lease_held_by_someone_else_is_not_this_containers_health(
    db_url: str,
) -> None:

    # a dead worker's lease can still look fresh for a while: it must not count as "us"
    hold_lease(db_url, "some-other-container:1", T + timedelta(minutes=10))
    assert cli.main(["worker-status", "--check", "--max-age", "1200"], clock=lambda: T) == 0
    assert (
        cli.main(["worker-status", "--check", "--mine", "--max-age", "1200"], clock=lambda: T) == 1
    )


def test_our_own_fresh_lease_is_healthy_with_mine(db_url: str) -> None:
    import socket

    hold_lease(db_url, f"{socket.gethostname()}:99", T + timedelta(minutes=10))
    assert (
        cli.main(["worker-status", "--check", "--mine", "--max-age", "1200"], clock=lambda: T) == 0
    )

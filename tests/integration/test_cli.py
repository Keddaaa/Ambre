"""`ambre` CLI: subprocess smoke test plus in-process tests with a fake Manifold client."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeManifoldClient, load_fixture
from pydantic import SecretStr
from sqlalchemy import func, select

from ambre import cli
from ambre.config.settings import Settings
from ambre.domain.models import SimulationStatus
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import SimulationRow
from ambre.portfolio.service import create_simulation

AMBRE = Path(sys.executable).parent / "ambre"
HEADER = "mode=PAPER orders_enabled=false"


def _clean_env(db_url: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AMBRE_", "MANIFOLD_", "EXECUTION_", "ENABLE_", "DATABASE_"))
    }
    env["DATABASE_URL"] = db_url
    return env


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'ambre.db'}"
    for key in list(os.environ):
        if key.startswith(("AMBRE_", "MANIFOLD_", "EXECUTION_", "ENABLE_", "DATABASE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)  # no stray .env
    return url


def _run(*args: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(AMBRE), *args], env=env, cwd=cwd, capture_output=True, text=True, timeout=60
    )


def _json_lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines()[1:] if line.strip()]


def test_cycle_subprocess_after_init_db(tmp_path: Path) -> None:
    env = _clean_env(f"sqlite:///{tmp_path / 'ambre.db'}")

    init = _run("init-db", env=env, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    assert init.stdout.splitlines()[0].startswith(HEADER)

    result = _run("cycle", env=env, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    first = result.stdout.splitlines()[0]
    assert first.startswith(HEADER)
    assert first == f"{HEADER} db=sqlite:///{tmp_path / 'ambre.db'}"
    (report,) = _json_lines(result.stdout)
    assert report["survival_state"] == "OBSERVING"  # never synced
    assert report["simulated"] == 0


def test_header_masks_password_and_never_prints_secrets() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://ambre:s3cr3t-pw@db:5432/ambre",
        manifold_api_key=SecretStr("api-key-xyz"),
    )
    line = cli.header_line(settings)
    assert line.startswith(HEADER)
    assert "s3cr3t-pw" not in line
    assert "api-key-xyz" not in line
    assert "postgresql+psycopg://ambre:***@db:5432/ambre" in line


def test_sync_uses_injected_client(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init-db"]) == 0
    capsys.readouterr()
    fake = FakeManifoldClient(load_fixture())

    code = cli.main(["sync", "--max-pages", "2"], client_factory=lambda s: fake)  # type: ignore[arg-type,return-value]

    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0].startswith(HEADER)
    (report,) = _json_lines(out)
    assert report["completed"] is True
    assert report["new_snapshots"] > 0
    assert fake.closed


def test_sync_failure_exits_non_zero(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init-db"]) == 0
    capsys.readouterr()
    code = cli.main(["sync"], client_factory=lambda s: FakeManifoldClient([], fail=True))  # type: ignore[arg-type,return-value]
    assert code == 1
    (report,) = _json_lines(capsys.readouterr().out)
    assert report["completed"] is False


def test_worker_runs_cycle_despite_sync_failure_and_ctrl_c_exits_0(
    db_url: str, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    assert cli.main(["init-db"]) == 0
    capsys.readouterr()
    sleeps: list[float] = []

    def interrupt(seconds: float) -> None:
        sleeps.append(seconds)
        raise KeyboardInterrupt

    with caplog.at_level(logging.WARNING):
        code = cli.main(
            ["worker", "--interval", "7"],
            client_factory=lambda s: FakeManifoldClient([], fail=True),  # type: ignore[arg-type,return-value]
            sleep=interrupt,
        )

    assert code == 0
    assert sleeps == [7]
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith(HEADER)
    (iteration,) = _json_lines(out)
    assert iteration["sync"]["completed"] is False
    assert iteration["cycle"]["survival_state"] == "OBSERVING"
    assert any("sync" in r.getMessage() for r in caplog.records)
    engine = make_engine(db_url)
    with make_session_factory(engine)() as s:
        assert s.scalar(select(func.count()).select_from(SimulationRow)) == 1
    engine.dispose()


def test_worker_sync_exception_still_runs_cycle(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init-db"]) == 0
    capsys.readouterr()

    def broken_factory(settings: Settings) -> Any:
        raise RuntimeError("cannot build client")

    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    code = cli.main(["worker"], client_factory=broken_factory, sleep=interrupt)

    assert code == 0
    (iteration,) = _json_lines(capsys.readouterr().out)
    assert iteration["sync"] is None
    assert iteration["cycle"]["survival_state"] == "OBSERVING"


def test_cycle_rolls_back_on_exception(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init-db"]) == 0

    def failing_cycle(session: Any, settings: Settings, now: Any) -> Any:
        create_simulation(session, "half-done", 1000.0, now)
        raise RuntimeError("boom mid-cycle")

    monkeypatch.setattr(cli, "run_cycle", failing_cycle)

    assert cli.main(["cycle"]) == 1
    engine = make_engine(db_url)
    with make_session_factory(engine)() as s:
        assert s.scalar(select(func.count()).select_from(SimulationRow)) == 0
    engine.dispose()


def test_new_simulation(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init-db"]) == 0
    capsys.readouterr()

    assert cli.main(["new-simulation", "--name", "exp-1"]) == 0

    (report,) = _json_lines(capsys.readouterr().out)
    assert report["name"] == "exp-1"
    assert report["initial_capital"] == 1000.0
    engine = make_engine(db_url)
    with make_session_factory(engine)() as s:
        sim = s.get(SimulationRow, report["simulation_id"])
        assert sim is not None and sim.status == SimulationStatus.ACTIVE.value
    engine.dispose()


def test_manifold_mode_is_refused(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init-db"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("EXECUTION_MODE", "MANIFOLD_MANA")
    monkeypatch.setenv("ENABLE_MANIFOLD_ORDERS", "true")

    assert cli.main(["cycle"]) == 2

    assert capsys.readouterr().out.startswith("mode=MANIFOLD_MANA orders_enabled=true")

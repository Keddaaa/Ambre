from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ambre.domain.models import BinaryMarket, MarketSnapshot
from ambre.infrastructure import repositories as repo
from ambre.infrastructure.db import UTCDateTime, make_engine, make_session_factory
from ambre.infrastructure.orm import Base, PositionRow, SimulationRow

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def mk_market(mid: str = "m1", **kw: object) -> BinaryMarket:
    base: dict[str, object] = dict(
        id=mid,
        question="q?",
        slug="q",
        url="https://x/q",
        creator_id="c",
        creator_username="cu",
        mechanism="cpmm-1",
        created_at=T0,
        close_at=None,
        is_resolved=False,
        resolution=None,
        resolution_probability=None,
        resolution_at=None,
        token="MANA",
        group_slugs=["a"],
    )
    base.update(kw)
    return BinaryMarket(**base)  # type: ignore[arg-type]


def mk_snap(
    mid: str = "m1", observed: datetime = T0, event: datetime | None = T0
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=mid,
        probability=0.4,
        pool_yes=100.0,
        pool_no=100.0,
        p=0.5,
        total_liquidity=None,
        volume=None,
        volume_24h=None,
        unique_bettor_count=None,
        observed_at=observed,
        event_at=event,
        source_id="s",
    )


def test_snapshot_dedup_by_event_at(session: Session) -> None:
    repo.upsert_market(session, mk_market(), {"a": 1}, T0)
    first = repo.insert_snapshot_if_new(session, mk_snap(), T0)
    second = repo.insert_snapshot_if_new(session, mk_snap(observed=T0 + timedelta(minutes=1)), T0)
    assert isinstance(first, int)
    assert second is None


def test_snapshot_without_event_at_inserts(session: Session) -> None:
    repo.upsert_market(session, mk_market(), {}, T0)
    a = repo.insert_snapshot_if_new(session, mk_snap(event=None), T0)
    b = repo.insert_snapshot_if_new(session, mk_snap(event=None), T0)
    assert a is not None and b is not None and a != b


def test_utc_roundtrip(session: Session) -> None:
    tz = timezone(timedelta(hours=2))
    repo.upsert_market(
        session, mk_market(created_at=datetime(2026, 1, 1, 12, 0, tzinfo=tz)), {}, T0
    )
    session.commit()
    session.expire_all()
    row = repo.get_market_detail(session, "m1")
    assert row is not None
    assert row.created_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert row.created_at.utcoffset() == timedelta(0)


def test_utc_rejects_naive() -> None:
    with pytest.raises(ValueError):
        UTCDateTime().process_bind_param(datetime(2026, 1, 1), None)  # type: ignore[arg-type]


def test_upsert_market_updates_and_keeps_first_seen(session: Session) -> None:
    repo.upsert_market(session, mk_market(), {"v": 1}, T0)
    later = T0 + timedelta(hours=1)
    row = repo.upsert_market(
        session, mk_market(is_resolved=True, resolution="YES"), {"v": 2}, later
    )
    assert row.first_seen_at == T0
    assert row.last_observed_at == later
    assert row.raw == {"v": 2}
    assert row.is_resolved is True


def test_resolved_outcomes_respect_cutoff(session: Session) -> None:
    cutoff = T0 + timedelta(days=10)
    specs = [
        ("early", "YES", cutoff - timedelta(days=1)),
        ("late", "NO", cutoff + timedelta(days=1)),
        ("cancel", "CANCEL", cutoff - timedelta(days=2)),
        ("no", "NO", cutoff - timedelta(days=3)),
    ]
    for mid, res, at in specs:
        repo.upsert_market(
            session, mk_market(mid, is_resolved=True, resolution=res, resolution_at=at), {}, T0
        )
    assert sorted(repo.resolved_binary_outcomes(session, cutoff)) == ["NO", "YES"]


def test_one_position_per_market(session: Session) -> None:
    repo.upsert_market(session, mk_market(), {}, T0)
    sim = repo.create_simulation(session, "s", 1000.0, T0)
    for _ in range(2):
        session.add(
            PositionRow(
                simulation_id=sim.id,
                market_id="m1",
                decision_id=None,
                side="YES",
                shares=1.0,
                cost=1.0,
                opened_at=T0,
                status="OPEN",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_latest_snapshots_for_open_markets(session: Session) -> None:
    repo.upsert_market(session, mk_market("a"), {}, T0)
    repo.upsert_market(session, mk_market("b"), {}, T0 + timedelta(hours=1))
    repo.upsert_market(session, mk_market("r", is_resolved=True), {}, T0)
    for mid, mins in [("a", 0), ("a", 5), ("b", 1), ("r", 0)]:
        t = T0 + timedelta(minutes=mins)
        repo.insert_snapshot_if_new(session, mk_snap(mid, observed=t, event=t), t)
    out = repo.latest_snapshots_for_open_markets(session, 10)
    assert [m.id for m, _ in out] == ["b", "a"]
    assert dict((m.id, s.observed_at) for m, s in out)["a"] == T0 + timedelta(minutes=5)
    assert len(repo.latest_snapshots_for_open_markets(session, 1)) == 1
    latest = repo.latest_snapshot_for_market(session, "a")
    assert latest is not None and latest.observed_at == T0 + timedelta(minutes=5)
    assert repo.latest_snapshot_for_market(session, "zzz") is None


def test_sync_state_ignored_simulation_control(session: Session) -> None:
    assert repo.get_sync_state(session, "k") is None
    repo.save_sync_state(
        session, "k", high_watermark_ms=5, last_success_at=T0, last_error=None, updated_at=T0
    )
    repo.save_sync_state(
        session, "k", high_watermark_ms=9, last_success_at=T0, last_error="e", updated_at=T0
    )
    st = repo.get_sync_state(session, "k")
    assert st is not None and st.high_watermark_ms == 9 and st.last_error == "e"

    repo.upsert_ignored(session, "x", "MULTIPLE_CHOICE", "unsupported", T0)
    row = repo.upsert_ignored(session, "x", "PERP", "other", T0)
    assert row.outcome_type == "PERP"

    assert repo.get_active_simulation(session) is None
    sim = repo.create_simulation(session, "sim", 1000.0, T0)
    assert sim.status == "ACTIVE"
    got = repo.get_active_simulation(session)
    assert got is not None and got.id == sim.id

    assert repo.get_control(session) is None
    repo.set_control(session, halted=True, reason="why", updated_at=T0)
    c = repo.get_control(session)
    assert c is not None and c.halted and c.reason == "why"
    repo.set_control(session, halted=False, reason=None, updated_at=T0)
    c = repo.get_control(session)
    assert c is not None and not c.halted
    assert isinstance(session.get(SimulationRow, sim.id), SimulationRow)


def test_list_functions_empty(session: Session) -> None:
    assert repo.list_markets(session, limit=5, offset=0) == []
    assert repo.get_market_detail(session, "nope") is None
    assert repo.list_decisions(session, "sim", limit=5, offset=0) == []
    assert repo.list_portfolio_snapshots(session, "sim") == []


def test_make_engine_creates_dir_and_fk(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path}/sub/x.db")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert (tmp_path / "sub").is_dir()


def test_alembic_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/m.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_sync_state_bigint_watermark(session: Session) -> None:
    ms = 1_793_454_346_285
    repo.save_sync_state(
        session, "w", high_watermark_ms=ms, last_success_at=T0, last_error=None, updated_at=T0
    )
    session.commit()
    session.expire_all()
    st = repo.get_sync_state(session, "w")
    assert st is not None and st.high_watermark_ms == ms


@pytest.mark.parametrize("file_db", [True, False])
def test_rollback_discards_work_done_in_a_savepoint(tmp_path: Path, file_db: bool) -> None:
    # pysqlite's legacy transaction handling would let RELEASE SAVEPOINT commit for real.
    from ambre.portfolio.service import create_simulation

    url = f"sqlite:///{tmp_path / 'tx.db'}" if file_db else "sqlite://"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        create_simulation(s, "rolled-back", 1000.0, T0)
        s.rollback()
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(SimulationRow)) == 0
    engine.dispose()

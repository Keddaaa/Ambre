"""Engine, session factory and the UTC-only datetime column type."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as UTC; always reads back aware UTC; rejects naive values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime rejected: timezone-aware value required")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"
    kwargs: dict[str, Any] = {}
    if is_sqlite:
        db = parsed.database
        if db in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            Path(db).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if is_sqlite:
        # pysqlite's legacy transaction handling emits no BEGIN before a SAVEPOINT, so
        # RELEASE SAVEPOINT would commit for real and an outer rollback would lose
        # nothing. Take over transaction control (SQLAlchemy's documented recipe).
        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.isolation_level = None
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine, "begin")
        def _on_begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)

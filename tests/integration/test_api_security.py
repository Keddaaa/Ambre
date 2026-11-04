"""Security of the local API: no session, so writes rest on Origin, content type and Host.

A web page can send a "simple" POST to localhost without any preflight. These tests replay what a
hostile page can do, on every route that changes state.
"""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from builders import SETTINGS, T_CYCLE, T_SYNC, cycle, sync
from fakes import load_fixture
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ambre.api.app import create_app
from ambre.infrastructure import repositories as repo
from ambre.infrastructure import worker_lease
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, DecisionRow, WorkerLeaseRow

NOW = T_CYCLE + timedelta(minutes=4)
DASHBOARD = "http://localhost:3000"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
BODIES: dict[str, dict[str, Any]] = {
    "/control/halt": {"reason": "forged request"},
    "/control/resume": {"confirm": True},
    "/control/run-cycle": {},
    "/orders": {
        "market_id": "synthetic_nope",
        "side": "YES",
        "probability": 0.6,
        "stake": 1.0,
        "actor": "USER",
        "request_id": "forged-request",
    },
}


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    made = make_session_factory(engine)
    sync(made, load_fixture(), T_SYNC)
    cycle(made)
    yield made
    engine.dispose()


def client(factory: sessionmaker[Session]) -> TestClient:
    settings = SETTINGS.model_copy(update={"cors_origins": [DASHBOARD]})
    return TestClient(create_app(settings, factory, clock=lambda: NOW), base_url="http://localhost")


def state(factory: sessionmaker[Session]) -> tuple[int, bool]:
    with factory() as s:
        control = repo.get_control(s)
        decisions = s.scalar(select(func.count()).select_from(DecisionRow))
        return decisions or 0, control.halted if control else False


def write_routes() -> set[str]:
    app = create_app(SETTINGS, None)
    return {
        r.path  # type: ignore[attr-defined]
        for r in app.routes
        if UNSAFE & set(getattr(r, "methods", None) or set())
    }


def test_every_write_route_has_a_forged_body_here() -> None:
    assert write_routes() == set(BODIES)  # a new write route must join these tests


@pytest.mark.parametrize("path", sorted(BODIES))
@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://localhost:3001"])
def test_foreign_origin_cannot_write(
    factory: sessionmaker[Session], path: str, origin: str
) -> None:
    before = state(factory)
    r = client(factory).post(path, json=BODIES[path], headers={"Origin": origin})
    assert r.status_code == 403
    assert state(factory) == before


@pytest.mark.parametrize("path", sorted(BODIES))
@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"],
)
def test_simple_request_cannot_write(
    factory: sessionmaker[Session], path: str, content_type: str | None
) -> None:
    """What a page can send without a preflight. ``/control/run-cycle`` reads no body, so FastAPI
    never looked at its content type: it accepted all of these before the guard."""
    before = state(factory)
    headers = {"content-type": content_type} if content_type else {}
    payload = '{"reason": "forged request", "confirm": true}'
    r = client(factory).post(path, content=payload, headers=headers)
    assert r.status_code == 415
    assert state(factory) == before


def test_empty_post_cannot_start_a_cycle(factory: sessionmaker[Session]) -> None:
    before = state(factory)
    assert client(factory).post("/control/run-cycle").status_code == 415
    assert state(factory) == before


def test_dashboard_origin_can_write(factory: sessionmaker[Session]) -> None:
    headers = {"Origin": DASHBOARD}
    c = client(factory)
    r = c.post("/control/halt", json={"reason": "manual check"}, headers=headers)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASHBOARD
    assert c.post("/control/resume", json={"confirm": True}, headers=headers).status_code == 200
    assert c.post("/control/run-cycle", json={}, headers=headers).status_code == 200


def test_json_content_type_with_charset_is_accepted(factory: sessionmaker[Session]) -> None:
    r = client(factory).post(
        "/control/halt",
        content='{"reason": "manual check"}',
        headers={"content-type": "application/json; charset=utf-8"},
    )
    assert r.status_code == 200


def test_foreign_preflight_is_not_allowed(factory: sessionmaker[Session]) -> None:
    r = client(factory).options(
        "/control/run-cycle",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "192.168.1.20:8000"])
def test_dns_rebinding_is_refused(factory: sessionmaker[Session], host: str) -> None:
    """A rebound name reaches 127.0.0.1 as same-origin: only the Host header gives it away."""
    before = state(factory)
    c = client(factory)
    assert c.get("/portfolio", headers={"Host": host}).status_code == 400
    r = c.post("/control/halt", json=BODIES["/control/halt"], headers={"Host": host})
    assert r.status_code == 400
    assert state(factory) == before


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "localhost"])
def test_loopback_hosts_are_served(factory: sessionmaker[Session], host: str) -> None:
    assert client(factory).get("/health", headers={"Host": host}).status_code == 200


def test_run_cycle_refused_while_a_worker_holds_the_lease(factory: sessionmaker[Session]) -> None:
    assert worker_lease.acquire(factory, "host:1", NOW, 600.0)
    before = state(factory)
    r = client(factory).post("/control/run-cycle", json={})
    assert r.status_code == 409
    assert state(factory) == before
    with factory() as s:
        row = s.get(WorkerLeaseRow, worker_lease.LEASE_NAME)
        assert row is not None and row.holder == "host:1"  # the worker keeps its lease


def test_run_cycle_frees_the_lease_when_done(factory: sessionmaker[Session]) -> None:
    assert client(factory).post("/control/run-cycle", json={}).status_code == 200
    with factory() as s:
        assert s.get(WorkerLeaseRow, worker_lease.LEASE_NAME) is None

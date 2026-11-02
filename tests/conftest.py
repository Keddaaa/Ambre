from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The research path builds its own HTTP client; tests must never reach a real provider."""
    import httpx

    from ambre import cli

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network disabled in tests", request=request)

    monkeypatch.setattr(
        cli, "default_http_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

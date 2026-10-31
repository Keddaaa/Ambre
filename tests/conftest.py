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

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import opensumma.kernel  # noqa: F401  (registers the kernel models on Base.metadata)
from opensumma.db import Base, create_engine


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """URL of a fresh, empty SQLite database file unique to the test."""
    return f"sqlite:///{tmp_path / 'opensumma.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session on an empty database with the current schema.

    The schema comes from the models rather than from Alembic, because it is far
    faster per test and ``test_models_match_migrations`` already proves the two
    agree. Acceptance tests go through ``init_db`` instead.
    """
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

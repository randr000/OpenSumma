from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from ledgerlab.db import create_engine


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """URL of a fresh, empty SQLite database file unique to the test."""
    return f"sqlite:///{tmp_path / 'ledgerlab.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()

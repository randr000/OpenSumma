"""Phase 0 acceptance: a fresh database can be created through the public API."""

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from ledgerlab.db import alembic_config, init_db


def test_fresh_sqlite_database_is_initialized_at_latest_schema(
    database_url: str, engine: Engine
) -> None:
    init_db(database_url)
    init_db(database_url)  # re-initializing an up-to-date database is a no-op

    head = ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()
    assert head is not None
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == head

"""Alembic migration environment for LedgerLab."""

from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from ledgerlab.db import Base, get_database_url
from ledgerlab.money import Money

# Import persistence model modules here as they are added, so that
# autogenerate can compare them against the database.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_database_url()


def _render_item(
    type_: str, obj: Any, autogen_context: AutogenContext
) -> str | Literal[False]:
    # Migrations record the physical column type rather than importing
    # application types, which may change after the migration is written.
    if type_ == "type" and isinstance(obj, Money):
        return "sa.BigInteger()"
    return False


def run_migrations_offline() -> None:
    """Emit migration SQL without a database connection (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A plain engine rather than ledgerlab.db.create_engine: SQLite foreign-key
    # enforcement stays off while migrating, because batch mode rebuilds tables
    # by dropping and recreating them, which would otherwise trigger ON DELETE
    # actions or fail on referencing rows.
    engine = create_engine(_database_url(), poolclass=NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most columns and constraints in place; batch
            # mode rebuilds the table there and emits plain ALTERs elsewhere.
            render_as_batch=True,
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

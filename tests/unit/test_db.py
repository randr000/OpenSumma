import pytest
from sqlalchemy import make_url

from opensumma.db import (
    DATABASE_URL_ENV,
    DEFAULT_DATABASE_URL,
    alembic_config,
    engine_url,
    get_database_url,
)


def test_database_url_defaults_to_local_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    assert get_database_url() == DEFAULT_DATABASE_URL


def test_database_url_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://ledger@db/ledgerlab")
    assert get_database_url() == "postgresql://ledger@db/ledgerlab"


def test_alembic_config_preserves_percent_encoded_urls() -> None:
    url = "postgresql://ledger:p%40ss@db/ledgerlab"
    assert alembic_config(url).get_main_option("sqlalchemy.url") == url


@pytest.mark.parametrize(
    ("url", "connects_with"),
    [
        # A PostgreSQL URL naming no driver uses psycopg 3, which the extra installs.
        ("postgresql://ledger:p%40ss@db:5433/books", "postgresql+psycopg"),
        ("postgres://ledger@db/books", "postgresql+psycopg"),
        # A driver named explicitly is kept, and other databases are left alone.
        ("postgresql+psycopg2://ledger@db/books", "postgresql+psycopg2"),
        ("sqlite:///opensumma.db", "sqlite"),
    ],
)
def test_engines_connect_to_postgresql_with_psycopg(
    url: str, connects_with: str
) -> None:
    connected = engine_url(url)
    assert connected.drivername == connects_with
    assert connected.database == make_url(url).database
    assert connected.password == make_url(url).password

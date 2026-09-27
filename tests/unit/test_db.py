import pytest

from opensumma.db import (
    DATABASE_URL_ENV,
    DEFAULT_DATABASE_URL,
    alembic_config,
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

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Engine, Integer, MetaData, Table, insert, select
from sqlalchemy.exc import StatementError

from opensumma.utc import UtcDateTime

metadata = MetaData()
stamps = Table(
    "stamps",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("at", UtcDateTime, nullable=True),
)


@pytest.fixture
def stamps_engine(engine: Engine) -> Engine:
    metadata.create_all(engine)
    return engine


def _stored(engine: Engine, value: datetime | None) -> datetime | None:
    with engine.begin() as connection:
        connection.execute(insert(stamps).values(at=value))
        return connection.scalar(select(stamps.c.at))


def test_timestamps_come_back_timezone_aware_in_utc(stamps_engine: Engine) -> None:
    written = datetime(2026, 3, 15, 12, 30, 45, 123456, tzinfo=UTC)

    read = _stored(stamps_engine, written)

    assert read == written
    assert read is not None
    assert read.utcoffset() == timedelta(0)


def test_timestamps_in_another_zone_are_normalised_to_utc(
    stamps_engine: Engine,
) -> None:
    written = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-5)))

    read = _stored(stamps_engine, written)

    assert read == written
    assert read == datetime(2026, 3, 15, 17, 0, tzinfo=UTC)


def test_missing_timestamps_round_trip(stamps_engine: Engine) -> None:
    assert _stored(stamps_engine, None) is None


def test_naive_timestamps_are_rejected(stamps_engine: Engine) -> None:
    with pytest.raises(StatementError):
        _stored(stamps_engine, datetime(2026, 3, 15, 12, 0))  # noqa: DTZ001

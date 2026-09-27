from datetime import UTC, date, datetime, timedelta

import pytest

from opensumma.utc import ensure_date, utcnow


def test_utcnow_is_timezone_aware_and_in_utc() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.tzinfo is UTC


def test_ensure_date_accepts_a_plain_date() -> None:
    assert ensure_date(date(2026, 3, 15)) == date(2026, 3, 15)


@pytest.mark.parametrize(
    "value", [datetime(2026, 3, 15, tzinfo=UTC), "2026-03-15", None]
)
def test_ensure_date_rejects_timestamps_and_anything_else(value: object) -> None:
    with pytest.raises(TypeError):
        ensure_date(value)

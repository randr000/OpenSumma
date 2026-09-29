from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from opensumma.utc import ensure_date, ensure_utc, utcnow


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


def test_ensure_utc_normalises_an_aware_timestamp_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    stamp = ensure_utc(datetime(2026, 3, 15, 9, 30, tzinfo=eastern))

    assert stamp == datetime(2026, 3, 15, 14, 30, tzinfo=UTC)
    assert stamp.tzinfo is UTC


def test_ensure_utc_refuses_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="naive"):
        ensure_utc(datetime(2026, 3, 15, 9, 30))  # noqa: DTZ001


@pytest.mark.parametrize("value", [date(2026, 3, 15), "2026-03-15T09:30:00Z", None])
def test_ensure_utc_refuses_anything_but_a_datetime(value: object) -> None:
    with pytest.raises(TypeError):
        ensure_utc(value)

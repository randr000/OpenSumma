from datetime import UTC, timedelta

from opensumma.utc import utcnow


def test_utcnow_is_timezone_aware_and_in_utc() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.tzinfo is UTC

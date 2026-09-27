"""Timestamps: timezone-aware UTC in Python, UTC in the database.

Accounting timestamps are always UTC. SQLite has no timezone-aware type and
drops ``tzinfo`` silently, so a value written as aware would come back naive and
compare unequal to what went in. ``UtcDateTime`` rejects naive values going in
and re-attaches UTC coming out, on every backend.

Accounting *dates* (a period's start, an entry's accounting date) are plain
``date`` values rather than timestamps, and use ``sqlalchemy.Date``.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """A timezone-aware timestamp, normalised to UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"{value} is naive; accounting timestamps must be timezone-aware UTC"
            )
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

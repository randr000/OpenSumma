"""Accounting period services.

A period is the date range that owns a posting. Two rules matter and neither can
be expressed as a portable table constraint, so both are enforced here:

- a period's end is not before its start;
- periods never overlap, so a date resolves to exactly one period.
"""

import calendar
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma.kernel.enums import PeriodStatus
from opensumma.kernel.errors import (
    ClosedPeriodError,
    DuplicateCodeError,
    InvalidPeriodRangeError,
    OverlappingPeriodError,
    UnknownPeriodError,
)
from opensumma.kernel.models import AccountingPeriod


def create_period(
    session: Session,
    *,
    code: str,
    start_date: date,
    end_date: date,
    status: PeriodStatus = PeriodStatus.OPEN,
) -> AccountingPeriod:
    """Add an accounting period covering ``start_date`` to ``end_date`` inclusive."""
    code = code.strip()
    if not code:
        raise ValueError("period code must not be empty")
    if end_date < start_date:
        raise InvalidPeriodRangeError(
            f"period {code} ends on {end_date} but starts on {start_date}"
        )
    if find_period(session, code) is not None:
        raise DuplicateCodeError(f"period code {code!r} already exists")

    clash = session.scalars(
        select(AccountingPeriod)
        .where(
            AccountingPeriod.start_date <= end_date,
            AccountingPeriod.end_date >= start_date,
        )
        .order_by(AccountingPeriod.start_date)
    ).first()
    if clash is not None:
        raise OverlappingPeriodError(
            f"period {code} ({start_date}..{end_date}) overlaps "
            f"{clash.code} ({clash.start_date}..{clash.end_date})"
        )

    period = AccountingPeriod(
        code=code, start_date=start_date, end_date=end_date, status=status
    )
    session.add(period)
    return period


def create_calendar_year_periods(session: Session, year: int) -> list[AccountingPeriod]:
    """Create the twelve monthly periods of a calendar year, coded ``YYYY-MM``."""
    return [
        create_period(
            session,
            code=f"{year:04d}-{month:02d}",
            start_date=date(year, month, 1),
            end_date=date(year, month, calendar.monthrange(year, month)[1]),
        )
        for month in range(1, 13)
    ]


def find_period(session: Session, code: str) -> AccountingPeriod | None:
    """Return the period with ``code``, or ``None``."""
    return session.scalars(
        select(AccountingPeriod).where(AccountingPeriod.code == code)
    ).one_or_none()


def get_period(session: Session, code: str) -> AccountingPeriod:
    """Return the period with ``code``, or raise ``UnknownPeriodError``."""
    period = find_period(session, code)
    if period is None:
        raise UnknownPeriodError(f"no accounting period with code {code!r}")
    return period


def period_for_date(session: Session, on: date) -> AccountingPeriod:
    """Return the period that owns ``on``.

    ``one_or_none`` rather than ``first``: two matching periods mean the
    non-overlap rule has been broken, and that must surface rather than be
    resolved arbitrarily.
    """
    period = session.scalars(
        select(AccountingPeriod).where(
            AccountingPeriod.start_date <= on, AccountingPeriod.end_date >= on
        )
    ).one_or_none()
    if period is None:
        raise UnknownPeriodError(f"no accounting period contains {on.isoformat()}")
    return period


def periods(session: Session) -> list[AccountingPeriod]:
    """Every accounting period, in chronological order."""
    return list(
        session.scalars(select(AccountingPeriod).order_by(AccountingPeriod.start_date))
    )


def assert_period_open(period: AccountingPeriod) -> None:
    """Raise unless ``period`` still accepts postings."""
    if not period.is_open:
        raise ClosedPeriodError(f"accounting period {period.code} is closed")


def close_period(period: AccountingPeriod) -> None:
    """Close ``period``, after which it accepts no postings."""
    period.status = PeriodStatus.CLOSED


def reopen_period(period: AccountingPeriod) -> None:
    """Reopen a closed period so that corrections can be posted into it."""
    period.status = PeriodStatus.OPEN

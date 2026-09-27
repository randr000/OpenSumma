from datetime import date
from itertools import pairwise

import pytest
from sqlalchemy.exc import IntegrityError
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
from opensumma.kernel.periods import (
    assert_period_open,
    close_period,
    create_calendar_year_periods,
    create_period,
    get_period,
    period_for_date,
    periods,
    reopen_period,
)


@pytest.fixture
def march(session: Session) -> AccountingPeriod:
    return create_period(
        session,
        code="2026-03",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
    )


def test_a_new_period_is_open(march: AccountingPeriod) -> None:
    assert march.status is PeriodStatus.OPEN
    assert march.is_open
    assert_period_open(march)


@pytest.mark.parametrize(
    ("on", "inside"),
    [
        (date(2026, 2, 28), False),
        (date(2026, 3, 1), True),
        (date(2026, 3, 15), True),
        (date(2026, 3, 31), True),
        (date(2026, 4, 1), False),
    ],
)
def test_a_period_covers_both_of_its_end_dates(
    march: AccountingPeriod, on: date, inside: bool
) -> None:
    assert march.contains(on) is inside


def test_an_accounting_date_resolves_to_its_period(
    session: Session, march: AccountingPeriod
) -> None:
    assert period_for_date(session, date(2026, 3, 15)) is march


def test_a_date_outside_every_period_has_no_period(
    session: Session, march: AccountingPeriod
) -> None:
    with pytest.raises(UnknownPeriodError):
        period_for_date(session, date(2026, 4, 1))


def test_a_period_may_not_end_before_it_starts(session: Session) -> None:
    with pytest.raises(InvalidPeriodRangeError):
        create_period(
            session,
            code="backwards",
            start_date=date(2026, 3, 31),
            end_date=date(2026, 3, 1),
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 3, 1), date(2026, 3, 31)),  # identical
        (date(2026, 3, 10), date(2026, 3, 20)),  # contained
        (date(2026, 1, 1), date(2026, 12, 31)),  # containing
        (date(2026, 2, 1), date(2026, 3, 1)),  # ends on the first day
        (date(2026, 3, 31), date(2026, 4, 30)),  # starts on the last day
    ],
)
def test_periods_may_not_overlap(
    session: Session, march: AccountingPeriod, start: date, end: date
) -> None:
    with pytest.raises(OverlappingPeriodError):
        create_period(session, code="clash", start_date=start, end_date=end)


def test_adjacent_periods_are_allowed(
    session: Session, march: AccountingPeriod
) -> None:
    april = create_period(
        session,
        code="2026-04",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert period_for_date(session, date(2026, 4, 1)) is april


def test_period_codes_are_unique(session: Session, march: AccountingPeriod) -> None:
    with pytest.raises(DuplicateCodeError):
        create_period(
            session,
            code="2026-03",
            start_date=date(2027, 3, 1),
            end_date=date(2027, 3, 31),
        )


def test_unknown_period_codes_are_reported(session: Session) -> None:
    with pytest.raises(UnknownPeriodError):
        get_period(session, "1999-13")


@pytest.mark.parametrize("year", [2025, 2024])
def test_a_calendar_year_is_twelve_contiguous_months(
    session: Session, year: int
) -> None:
    created = create_calendar_year_periods(session, year)

    assert [period.code for period in created] == [
        f"{year}-{month:02d}" for month in range(1, 13)
    ]
    assert created[0].start_date == date(year, 1, 1)
    assert created[-1].end_date == date(year, 12, 31)
    for earlier, later in pairwise(created):
        assert (later.start_date - earlier.end_date).days == 1


def test_february_ends_on_the_leap_day_in_a_leap_year(session: Session) -> None:
    created = create_calendar_year_periods(session, 2024)

    assert created[1].end_date == date(2024, 2, 29)


def test_a_closed_period_accepts_no_postings(march: AccountingPeriod) -> None:
    close_period(march)

    assert march.status is PeriodStatus.CLOSED
    assert not march.is_open
    with pytest.raises(ClosedPeriodError):
        assert_period_open(march)


def test_a_reopened_period_accepts_postings_again(march: AccountingPeriod) -> None:
    close_period(march)
    reopen_period(march)

    assert_period_open(march)


def test_periods_are_listed_chronologically(session: Session) -> None:
    create_period(
        session, code="2026-04", start_date=date(2026, 4, 1), end_date=date(2026, 4, 30)
    )
    create_period(
        session, code="2026-01", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )

    assert [period.code for period in periods(session)] == ["2026-01", "2026-04"]


def test_the_database_rejects_a_period_that_ends_before_it_starts(
    session: Session,
) -> None:
    session.add(
        AccountingPeriod(
            code="backwards",
            start_date=date(2026, 3, 31),
            end_date=date(2026, 3, 1),
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()

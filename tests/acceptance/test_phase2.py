"""Phase 2 acceptance: journal entries and deterministic validation.

Everything here goes through the public kernel API against a database created by
the migrations, the way a caller outside the project would use it.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from opensumma.db import init_db
from opensumma.kernel import (
    ImmutableEntryError,
    IssueCode,
    JournalEntry,
    JournalEntryError,
    JournalEntryStatus,
    LineInput,
    close_period,
    create_calendar_year_periods,
    create_journal_entry,
    deactivate_account,
    get_account,
    get_journal_entry,
    get_period,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    seed_dimensions,
    validate_journal_entry,
)

MARCH = date(2026, 3, 15)


@pytest.fixture
def books(database_url: str, engine: Engine) -> Iterator[Session]:
    """A migrated database holding the default chart, dimensions, and 2026."""
    init_db(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        create_calendar_year_periods(session, 2026)
        session.commit()
        yield session


def _record(session: Session, *lines: LineInput, on: date = MARCH) -> JournalEntry:
    return create_journal_entry(
        session, entry_date=on, description="Acceptance", lines=list(lines)
    )


def _codes(issues: object) -> set[IssueCode]:
    return {issue.code for issue in issues}  # type: ignore[attr-defined]


def _ledger_totals(session: Session) -> tuple[int, int]:
    debits, credits = session.execute(
        text(
            "SELECT COALESCE(SUM(l.debit), 0), COALESCE(SUM(l.credit), 0) "
            "FROM journal_line l JOIN journal_entry e ON e.id = l.journal_entry_id "
            "WHERE e.status IN ('POSTED', 'REVERSED')"
        )
    ).one()
    return int(debits), int(credits)


def test_journal_entries_and_their_lines_exist(books: Session) -> None:
    entry = _record(
        books,
        LineInput("6100", debit=Decimal("120.50"), dimensions={"DEPARTMENT": "ENG"}),
        LineInput("1111", credit=Decimal("120.50")),
    )
    books.commit()
    books.expire_all()

    stored = get_journal_entry(books, entry.id)
    assert stored.status is JournalEntryStatus.DRAFT
    assert [(line.line_number, line.account.code) for line in stored.lines] == [
        (1, "6100"),
        (2, "1111"),
    ]
    assert stored.lines[0].dimensions[0].value.code == "ENG"


def test_amounts_use_exact_decimal_arithmetic(books: Session) -> None:
    entry = _record(
        books,
        LineInput("6100", debit=Decimal("0.10")),
        LineInput("6200", debit=Decimal("0.20")),
        LineInput("1111", credit=Decimal("0.30")),
    )
    post_journal_entry(books, entry)
    books.commit()
    books.expire_all()

    assert all(isinstance(line.debit, Decimal) for line in entry.lines)
    assert entry.total_debits == Decimal("0.30")  # 0.1 + 0.2 != 0.3 in floats
    assert _ledger_totals(books) == (30, 30)


def test_balanced_entries_validate(books: Session) -> None:
    entry = _record(
        books,
        LineInput("6300", debit=Decimal("10000.00")),
        LineInput("6310", debit=Decimal("765.00")),
        LineInput("2130", credit=Decimal("2765.00")),
        LineInput("1112", credit=Decimal("8000.00")),
    )

    assert validate_journal_entry(books, entry) == []
    post_journal_entry(books, entry)
    assert entry.status is JournalEntryStatus.POSTED


def test_unbalanced_entries_fail(books: Session) -> None:
    entry = _record(
        books,
        LineInput("6100", debit=Decimal("100.00")),
        LineInput("1111", credit=Decimal("99.99")),
    )

    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, entry)
    assert _codes(caught.value.issues) == {IssueCode.UNBALANCED}


def test_invalid_accounts_fail(books: Session) -> None:
    with pytest.raises(JournalEntryError) as unknown:
        _record(
            books,
            LineInput("9999", debit=Decimal("5.00")),
            LineInput("1111", credit=Decimal("5.00")),
        )
    assert _codes(unknown.value.issues) == {IssueCode.UNKNOWN_ACCOUNT}

    deactivate_account(get_account(books, "6900"))
    entry = _record(
        books,
        LineInput("6000", debit=Decimal("5.00")),
        LineInput("6900", debit=Decimal("5.00")),
        LineInput("1111", credit=Decimal("10.00")),
    )
    assert _codes(validate_journal_entry(books, entry)) == {
        IssueCode.ACCOUNT_NOT_POSTABLE,
        IssueCode.ACCOUNT_INACTIVE,
    }


def test_closed_periods_fail(books: Session) -> None:
    close_period(get_period(books, "2026-03"))
    entry = _record(
        books,
        LineInput("6100", debit=Decimal("5.00")),
        LineInput("1111", credit=Decimal("5.00")),
    )

    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, entry)
    assert _codes(caught.value.issues) == {IssueCode.PERIOD_CLOSED}


def test_posted_entries_cannot_be_mutated(books: Session) -> None:
    entry = _record(
        books,
        LineInput("6100", debit=Decimal("5.00")),
        LineInput("1111", credit=Decimal("5.00")),
    )
    post_journal_entry(books, entry)
    books.commit()

    entry.lines[0].debit = Decimal("50.00")
    entry.lines[1].credit = Decimal("50.00")
    with pytest.raises(ImmutableEntryError):
        books.commit()
    books.rollback()

    assert entry.total_debits == Decimal("5.00")


def test_reversals_create_new_entries(books: Session) -> None:
    original = _record(
        books,
        LineInput("6100", debit=Decimal("5.00")),
        LineInput("1111", credit=Decimal("5.00")),
    )
    post_journal_entry(books, original)
    books.commit()

    reversal = reverse_journal_entry(books, original, entry_date=date(2026, 4, 1))
    books.commit()

    assert reversal.id != original.id
    assert reversal.reversal_of is original
    assert original.status is JournalEntryStatus.REVERSED
    assert reversal.status is JournalEntryStatus.POSTED
    assert _ledger_totals(books) == (1000, 1000)  # both stay in the ledger

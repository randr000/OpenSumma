"""Recorded journal entries cannot be changed, even by writing to the ORM directly.

Every entry here is committed before it is tampered with, so its attributes have
been expired and SQLAlchemy no longer remembers their previous values: the guard
has to work from what the database recorded, not from attribute history.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, insert, update
from sqlalchemy.orm import Session

from opensumma.kernel.accounts import get_account
from opensumma.kernel.dimensions import get_dimension_value
from opensumma.kernel.enums import JournalEntryStatus
from opensumma.kernel.errors import ImmutableEntryError, JournalEntryError
from opensumma.kernel.journal import (
    LineInput,
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    void_journal_entry,
)
from opensumma.kernel.models import JournalEntry, JournalLine, JournalLineDimension
from opensumma.utc import utcnow

MARCH = date(2026, 3, 15)


def _record(session: Session, description: str = "Rent") -> JournalEntry:
    return create_journal_entry(
        session,
        entry_date=MARCH,
        description=description,
        lines=[
            LineInput("6200", debit=Decimal("2500.00"), dimensions={"LOCATION": "HQ"}),
            LineInput("1111", credit=Decimal("2500.00")),
        ],
    )


@pytest.fixture
def posted(books: Session) -> JournalEntry:
    entry = _record(books)
    post_journal_entry(books, entry)
    books.commit()
    return entry


Tamper = Callable[[Session, JournalEntry], object]


def _set(attribute: str, value: object) -> Tamper:
    return lambda session, entry: setattr(entry, attribute, value)


def _add_line(session: Session, entry: JournalEntry) -> None:
    entry.lines.append(
        JournalLine(
            line_number=3, account=get_account(session, "6100"), debit=Decimal("1.00")
        )
    )


def _swap_dimension(session: Session, entry: JournalEntry) -> None:
    entry.lines[0].dimensions[0].value = get_dimension_value(
        session, "LOCATION", "WEST"
    )


def _tag_dimension(session: Session, entry: JournalEntry) -> None:
    entry.lines[1].dimensions.append(
        JournalLineDimension(value=get_dimension_value(session, "DEPARTMENT", "GA"))
    )


def _move_line_into_draft(session: Session, entry: JournalEntry) -> None:
    draft = _record(session, description="Draft")
    line = entry.lines[0]
    line.line_number = 3
    draft.lines.append(line)


TAMPERING: dict[str, Tamper] = {
    "description": _set("description", "Rent, adjusted"),
    "entry date": _set("entry_date", date(2026, 3, 1)),
    "posted_at": _set("posted_at", utcnow()),
    "back to draft": _set("status", JournalEntryStatus.DRAFT),
    "voided": _set("status", JournalEntryStatus.VOIDED),
    "line amount": lambda s, e: setattr(e.lines[0], "debit", Decimal("2400.00")),
    "line account": lambda s, e: setattr(e.lines[0], "account", get_account(s, "6100")),
    "line memo": lambda s, e: setattr(e.lines[0], "memo", "backdated"),
    "extra line": _add_line,
    "removed line": lambda s, e: e.lines.pop(),
    "deleted line": lambda s, e: s.delete(e.lines[0]),
    "line moved out": _move_line_into_draft,
    "dimension value": _swap_dimension,
    "dimension added": _tag_dimension,
    "dimension removed": lambda s, e: e.lines[0].dimensions.clear(),
    "deleted entry": lambda s, e: s.delete(e),
}


@pytest.mark.parametrize("tamper", TAMPERING.values(), ids=TAMPERING.keys())
def test_a_posted_entry_cannot_be_changed(
    books: Session, posted: JournalEntry, tamper: Tamper
) -> None:
    tamper(books, posted)

    with pytest.raises(ImmutableEntryError):
        books.flush()


def test_nothing_reaches_the_database_when_a_change_is_refused(
    books: Session, posted: JournalEntry
) -> None:
    posted.lines[0].debit = Decimal("1.00")
    with pytest.raises(ImmutableEntryError):
        books.commit()
    books.rollback()

    assert posted.lines[0].debit == Decimal("2500.00")
    assert posted.status is JournalEntryStatus.POSTED


def test_a_reversed_entry_cannot_be_changed_back(
    books: Session, posted: JournalEntry
) -> None:
    reverse_journal_entry(books, posted, entry_date=MARCH)
    books.commit()

    posted.status = JournalEntryStatus.POSTED
    with pytest.raises(ImmutableEntryError):
        books.flush()


def test_a_voided_entry_cannot_be_changed(books: Session) -> None:
    entry = _record(books)
    void_journal_entry(entry)
    books.commit()

    entry.description = "Revived"
    with pytest.raises(ImmutableEntryError):
        books.flush()


def test_a_line_cannot_be_moved_into_a_posted_entry(
    books: Session, posted: JournalEntry
) -> None:
    draft = _record(books, description="Draft")
    books.commit()

    line = draft.lines[0]
    line.line_number = 3
    posted.lines.append(line)
    with pytest.raises(ImmutableEntryError):
        books.flush()


@pytest.mark.parametrize(
    "statement",
    [
        update(JournalLine).values(memo="x"),
        update(JournalEntry).values(description="x"),
        delete(JournalLineDimension),
        insert(JournalLine).values(
            journal_entry_id=1, line_number=3, account_id=1, debit=1, credit=0
        ),
    ],
    ids=["update lines", "update entries", "delete dimensions", "insert a line"],
)
def test_bulk_writes_to_journal_tables_are_refused(
    books: Session, posted: JournalEntry, statement: object
) -> None:
    with pytest.raises(ImmutableEntryError):
        books.execute(statement)  # type: ignore[call-overload]


def test_drafts_remain_editable(books: Session) -> None:
    draft = _record(books)
    books.commit()

    draft.description = "Rent, corrected"
    draft.lines[0].debit = Decimal("2400.00")
    draft.lines[1].credit = Decimal("2400.00")
    draft.lines[0].dimensions.clear()
    books.commit()

    assert draft.description == "Rent, corrected"
    assert draft.total_debits == Decimal("2400.00")


def test_an_unbalanced_entry_cannot_be_written_straight_into_the_ledger(
    books: Session,
) -> None:
    """The balance rule holds even for code that bypasses the posting service."""
    books.add(
        JournalEntry(
            entry_date=MARCH,
            description="Forged",
            status=JournalEntryStatus.POSTED,
            posted_at=utcnow(),
            lines=[
                JournalLine(
                    line_number=1,
                    account=get_account(books, "6100"),
                    debit=Decimal("100.00"),
                ),
                JournalLine(
                    line_number=2,
                    account=get_account(books, "1111"),
                    credit=Decimal("1.00"),
                ),
            ],
        )
    )

    with pytest.raises(JournalEntryError, match=r"debits total 100\.00"):
        books.flush()


def test_a_draft_cannot_be_flipped_into_the_ledger_unbalanced(books: Session) -> None:
    draft = _record(books)
    draft.lines[0].debit = Decimal("1.00")
    books.commit()

    draft.status = JournalEntryStatus.POSTED
    draft.posted_at = utcnow()
    with pytest.raises(JournalEntryError):
        books.flush()


@pytest.mark.parametrize(
    "status", [JournalEntryStatus.PENDING_APPROVAL, JournalEntryStatus.APPROVED]
)
@pytest.mark.parametrize(
    "tamper",
    [
        _set("description", "Rent, adjusted"),
        _set("entry_date", date(2026, 3, 1)),
        lambda s, e: setattr(e.lines[0], "debit", Decimal("2400.00")),
        _add_line,
        _swap_dimension,
        lambda s, e: s.delete(e.lines[0]),
    ],
    ids=[
        "description",
        "entry date",
        "line amount",
        "extra line",
        "dimension",
        "deleted line",
    ],
)
def test_an_entry_under_review_or_approved_has_its_content_locked(
    books: Session, status: JournalEntryStatus, tamper: Tamper
) -> None:
    entry = _record(books)
    entry.status = status
    books.commit()

    tamper(books, entry)
    with pytest.raises(ImmutableEntryError):
        books.commit()


@pytest.mark.parametrize(
    ("status", "next_status"),
    [
        (JournalEntryStatus.PENDING_APPROVAL, JournalEntryStatus.APPROVED),
        (JournalEntryStatus.PENDING_APPROVAL, JournalEntryStatus.PROPOSED),
        (JournalEntryStatus.APPROVED, JournalEntryStatus.VOIDED),
    ],
)
def test_a_locked_entry_may_still_move_through_the_workflow(
    books: Session, status: JournalEntryStatus, next_status: JournalEntryStatus
) -> None:
    entry = _record(books)
    entry.status = status
    books.commit()

    entry.status = next_status
    books.commit()
    assert entry.status is next_status


def test_a_locked_entry_is_voided_not_deleted(books: Session) -> None:
    entry = _record(books)
    entry.status = JournalEntryStatus.APPROVED
    books.commit()

    books.delete(entry)
    with pytest.raises(ImmutableEntryError, match="void it instead"):
        books.commit()


def test_a_rejected_entry_is_editable_again(books: Session) -> None:
    entry = _record(books)
    entry.status = JournalEntryStatus.PENDING_APPROVAL
    books.commit()
    entry.status = JournalEntryStatus.PROPOSED
    books.commit()

    entry.description = "Rent, corrected"
    books.commit()
    assert entry.description == "Rent, corrected"

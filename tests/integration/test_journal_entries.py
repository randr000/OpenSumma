from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opensumma.kernel.accounts import deactivate_account, get_account
from opensumma.kernel.dimensions import get_dimension_value
from opensumma.kernel.enums import JournalEntryStatus
from opensumma.kernel.errors import (
    AlreadyPostedError,
    EntryStatusError,
    JournalEntryError,
    UnknownJournalEntryError,
    ValidationIssue,
)
from opensumma.kernel.journal import (
    LineInput,
    create_journal_entry,
    get_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    validate_journal_entry,
    void_journal_entry,
)
from opensumma.kernel.models import JournalEntry
from opensumma.kernel.periods import close_period, get_period

MARCH = date(2026, 3, 15)


def debit(account: str, amount: str, **dimensions: str) -> LineInput:
    return LineInput(account, debit=Decimal(amount), dimensions=dimensions)


def credit(account: str, amount: str, **dimensions: str) -> LineInput:
    return LineInput(account, credit=Decimal(amount), dimensions=dimensions)


def record(
    session: Session, *lines: LineInput, on: date = MARCH, description: str = "Test"
) -> JournalEntry:
    return create_journal_entry(
        session, entry_date=on, description=description, lines=list(lines)
    )


def codes(issues: list[ValidationIssue] | tuple[ValidationIssue, ...]) -> list[str]:
    return [
        issue.code.value
        if issue.line_number is None
        else f"{issue.code.value}@{issue.line_number}"
        for issue in issues
    ]


def rejected(
    session: Session, *lines: LineInput, description: str = "Test"
) -> list[str]:
    with pytest.raises(JournalEntryError) as caught:
        record(session, *lines, description=description)
    return codes(caught.value.issues)


# --- Recording ------------------------------------------------------------------


def test_a_balanced_entry_is_recorded_as_a_draft_with_numbered_lines(
    books: Session,
) -> None:
    entry = record(
        books, debit("6100", "120.50"), credit("1111", "120.50"), description="AWS"
    )
    books.commit()

    assert entry.status is JournalEntryStatus.DRAFT
    assert entry.posted_at is None
    assert entry.description == "AWS"
    assert [(line.line_number, line.account.code) for line in entry.lines] == [
        (1, "6100"),
        (2, "1111"),
    ]
    assert get_journal_entry(books, entry.id) is entry


def test_amounts_are_recorded_and_read_back_as_exact_decimals(books: Session) -> None:
    entry = record(
        books,
        debit("6100", "0.10"),
        debit("6200", "0.20"),
        credit("1111", "0.30"),
    )
    books.commit()
    books.expire_all()

    assert [line.debit for line in entry.lines] == [
        Decimal("0.10"),
        Decimal("0.20"),
        Decimal("0.00"),
    ]
    assert entry.total_debits == entry.total_credits == Decimal("0.30")


def test_a_line_is_never_both_a_debit_and_a_credit(books: Session) -> None:
    both = LineInput("6100", debit=Decimal("5.00"), credit=Decimal("5.00"))
    assert rejected(books, both, credit("1111", "5.00")) == ["DEBIT_AND_CREDIT@1"]


@pytest.mark.parametrize(
    "line",
    [
        LineInput("6100", debit=Decimal("-5.00")),
        LineInput("6100", credit=Decimal("-5.00")),
        LineInput("6100", debit=Decimal("10.00"), credit=Decimal("-5.00")),
    ],
)
def test_negative_amounts_are_rejected(books: Session, line: LineInput) -> None:
    assert rejected(books, line, credit("1111", "5.00")) == ["NEGATIVE_AMOUNT@1"]


def test_zero_amount_lines_are_rejected(books: Session) -> None:
    assert rejected(books, LineInput("6100"), credit("1111", "5.00")) == [
        "ZERO_AMOUNT@1"
    ]


@pytest.mark.parametrize(
    "amount",
    [Decimal("0.005"), Decimal("NaN"), 0.1, 5],
    ids=["fraction-of-a-cent", "nan", "float", "int"],
)
def test_amounts_are_never_rounded_or_coerced(books: Session, amount: object) -> None:
    line = LineInput("6100", debit=amount)  # type: ignore[arg-type]
    assert rejected(books, line, credit("1111", "5.00")) == ["INVALID_AMOUNT@1"]


def test_an_unknown_account_is_rejected(books: Session) -> None:
    assert rejected(books, debit("9999", "5.00"), credit("1111", "5.00")) == [
        "UNKNOWN_ACCOUNT@1"
    ]


def test_an_unknown_dimension_or_dimension_value_is_rejected(books: Session) -> None:
    assert rejected(
        books,
        debit("6100", "5.00", PROJECT="APOLLO"),
        credit("1111", "5.00", DEPARTMENT="LEGAL"),
    ) == ["UNKNOWN_DIMENSION@1", "UNKNOWN_DIMENSION_VALUE@2"]


def test_a_description_is_required_and_bounded(books: Session) -> None:
    lines = (debit("6100", "5.00"), credit("1111", "5.00"))
    assert rejected(books, *lines, description="   ") == ["MISSING_DESCRIPTION"]
    assert rejected(books, *lines, description="x" * 501) == ["TEXT_TOO_LONG"]


def test_every_problem_is_reported_at_once_and_nothing_is_recorded(
    books: Session,
) -> None:
    issues = rejected(
        books,
        debit("9999", "-1.00"),
        LineInput("6100", debit=Decimal("1.00"), credit=Decimal("1.00")),
        LineInput("1111"),
        LineInput("1111", debit=Decimal("0.001"), dimensions={"PROJECT": "X"}),
        description="",
    )

    assert issues == [
        "MISSING_DESCRIPTION",
        "NEGATIVE_AMOUNT@1",
        "UNKNOWN_ACCOUNT@1",
        "DEBIT_AND_CREDIT@2",
        "ZERO_AMOUNT@3",
        "INVALID_AMOUNT@4",
        "UNKNOWN_DIMENSION@4",
    ]
    assert books.scalars(select(JournalEntry)).all() == []


def test_an_accounting_date_is_a_date_not_a_timestamp(books: Session) -> None:
    with pytest.raises(TypeError):
        create_journal_entry(
            books,
            entry_date=datetime(2026, 3, 15, tzinfo=UTC),
            description="Test",
            lines=[debit("6100", "5.00"), credit("1111", "5.00")],
        )


def test_lines_carry_their_dimension_values(books: Session) -> None:
    entry = record(
        books,
        debit("6100", "5.00", DEPARTMENT="ENG", LOCATION="HQ"),
        credit("1111", "5.00"),
    )
    books.commit()

    first, second = entry.lines
    assert {tag.value.dimension.code: tag.value.code for tag in first.dimensions} == {
        "DEPARTMENT": "ENG",
        "LOCATION": "HQ",
    }
    assert second.dimensions == []


def test_an_unknown_entry_id_is_reported(books: Session) -> None:
    with pytest.raises(UnknownJournalEntryError):
        get_journal_entry(books, 4242)


# --- Validation and posting -------------------------------------------------------


def test_a_balanced_entry_validates_and_posts(books: Session) -> None:
    entry = record(books, debit("6100", "120.50"), credit("1111", "120.50"))
    assert validate_journal_entry(books, entry) == []

    before = datetime.now(UTC)
    post_journal_entry(books, entry)
    books.commit()

    assert entry.status is JournalEntryStatus.POSTED
    assert entry.posted_at is not None
    assert entry.posted_at.utcoffset() == timedelta(0)
    assert entry.posted_at >= before - timedelta(seconds=1)


def test_a_multi_line_entry_posts(books: Session) -> None:
    payroll = record(
        books,
        debit("6300", "10000.00", DEPARTMENT="ENG"),
        debit("6300", "6000.00", DEPARTMENT="SALES"),
        debit("6310", "1224.00"),
        credit("2130", "4424.00"),
        credit("1112", "12800.00"),
        description="March payroll",
    )

    post_journal_entry(books, payroll)

    assert payroll.status is JournalEntryStatus.POSTED
    assert payroll.total_debits == payroll.total_credits == Decimal("17224.00")


def test_an_unbalanced_entry_is_drafted_but_cannot_post(books: Session) -> None:
    entry = record(books, debit("6100", "100.00"), credit("1111", "99.99"))

    assert codes(validate_journal_entry(books, entry)) == ["UNBALANCED"]
    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, entry)
    assert codes(caught.value.issues) == ["UNBALANCED"]
    assert entry.status is JournalEntryStatus.DRAFT


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ((), ["TOO_FEW_LINES"]),
        ((debit("6100", "5.00"),), ["TOO_FEW_LINES", "UNBALANCED"]),
    ],
    ids=["no-lines", "single-line"],
)
def test_an_entry_needs_at_least_two_lines(
    books: Session, lines: tuple[LineInput, ...], expected: list[str]
) -> None:
    entry = record(books, *lines)
    assert codes(validate_journal_entry(books, entry)) == expected


def test_posting_to_a_parent_account_fails(books: Session) -> None:
    entry = record(books, debit("6000", "5.00"), credit("1111", "5.00"))
    assert codes(validate_journal_entry(books, entry)) == ["ACCOUNT_NOT_POSTABLE@1"]


def test_posting_to_an_inactive_account_fails(books: Session) -> None:
    deactivate_account(get_account(books, "6100"))
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    assert codes(validate_journal_entry(books, entry)) == ["ACCOUNT_INACTIVE@1"]


def test_posting_into_a_closed_period_fails(books: Session) -> None:
    close_period(get_period(books, "2026-03"))
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    assert codes(validate_journal_entry(books, entry)) == ["PERIOD_CLOSED"]


def test_posting_on_a_date_outside_every_period_fails(books: Session) -> None:
    entry = record(
        books, debit("6100", "5.00"), credit("1111", "5.00"), on=date(2027, 1, 4)
    )
    assert codes(validate_journal_entry(books, entry)) == ["NO_PERIOD"]


def test_posting_with_a_retired_dimension_value_fails(books: Session) -> None:
    entry = record(
        books, debit("6100", "5.00", DEPARTMENT="MKT"), credit("1111", "5.00")
    )
    get_dimension_value(books, "DEPARTMENT", "MKT").is_active = False

    assert codes(validate_journal_entry(books, entry)) == ["DIMENSION_VALUE_INACTIVE@1"]


def test_master_data_is_rechecked_at_the_moment_of_posting(books: Session) -> None:
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    assert validate_journal_entry(books, entry) == []

    deactivate_account(get_account(books, "1111"))

    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, entry)
    assert codes(caught.value.issues) == ["ACCOUNT_INACTIVE@2"]


def test_issues_are_reported_in_a_stable_order(books: Session) -> None:
    deactivate_account(get_account(books, "1111"))
    close_period(get_period(books, "2026-03"))
    entry = record(books, debit("6000", "5.00"), credit("1111", "4.00"))

    assert codes(validate_journal_entry(books, entry)) == [
        "UNBALANCED",
        "PERIOD_CLOSED",
        "ACCOUNT_NOT_POSTABLE@1",
        "ACCOUNT_INACTIVE@2",
    ]


def test_posting_twice_is_rejected(books: Session) -> None:
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    post_journal_entry(books, entry)
    books.commit()

    with pytest.raises(AlreadyPostedError):
        post_journal_entry(books, entry)


def test_a_draft_can_be_voided_and_then_never_posted(books: Session) -> None:
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))

    void_journal_entry(entry)
    books.commit()

    assert entry.status is JournalEntryStatus.VOIDED
    with pytest.raises(EntryStatusError):
        post_journal_entry(books, entry)
    with pytest.raises(EntryStatusError):
        void_journal_entry(entry)


def test_a_posted_entry_is_reversed_not_voided(books: Session) -> None:
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    post_journal_entry(books, entry)

    with pytest.raises(EntryStatusError, match="reversed, not voided"):
        void_journal_entry(entry)


# --- Reversal ---------------------------------------------------------------------


@pytest.fixture
def posted(books: Session) -> JournalEntry:
    entry = record(
        books,
        debit("6100", "120.50", DEPARTMENT="ENG"),
        debit("6200", "79.50"),
        credit("1111", "200.00"),
        description="March costs",
    )
    post_journal_entry(books, entry)
    books.commit()
    return entry


def test_a_reversal_is_a_new_posted_entry_with_every_side_swapped(
    books: Session, posted: JournalEntry
) -> None:
    reversal = reverse_journal_entry(books, posted, entry_date=date(2026, 4, 1))
    books.commit()

    assert reversal.id != posted.id
    assert reversal.status is JournalEntryStatus.POSTED
    assert reversal.entry_date == date(2026, 4, 1)
    assert reversal.reversal_of is posted
    assert posted.reversed_by is reversal
    assert posted.status is JournalEntryStatus.REVERSED
    assert [
        (line.account.code, line.debit, line.credit) for line in reversal.lines
    ] == [(line.account.code, line.credit, line.debit) for line in posted.lines]
    assert reversal.description == f"Reversal of journal entry {posted.id}"


def test_an_entry_and_its_reversal_net_every_account_to_zero(
    books: Session, posted: JournalEntry
) -> None:
    reversal = reverse_journal_entry(books, posted, entry_date=date(2026, 4, 1))

    net: defaultdict[str, Decimal] = defaultdict(Decimal)
    for line in (*posted.lines, *reversal.lines):
        net[line.account.code] += line.debit - line.credit
    assert set(net.values()) == {Decimal("0.00")}


def test_a_reversal_keeps_the_dimension_values_and_memos(
    books: Session, posted: JournalEntry
) -> None:
    reversal = reverse_journal_entry(
        books, posted, entry_date=date(2026, 4, 1), description="Wrong vendor"
    )

    assert reversal.description == "Wrong vendor"
    assert [tag.value.code for tag in reversal.lines[0].dimensions] == ["ENG"]


def test_an_entry_can_be_reversed_only_once(
    books: Session, posted: JournalEntry
) -> None:
    reversal = reverse_journal_entry(books, posted, entry_date=date(2026, 4, 1))
    books.commit()

    with pytest.raises(
        EntryStatusError, match=f"reversed by journal entry {reversal.id}"
    ):
        reverse_journal_entry(books, posted, entry_date=date(2026, 4, 2))


def test_a_reversal_can_itself_be_reversed(
    books: Session, posted: JournalEntry
) -> None:
    reversal = reverse_journal_entry(books, posted, entry_date=date(2026, 4, 1))

    reinstated = reverse_journal_entry(books, reversal, entry_date=date(2026, 4, 2))

    assert reversal.status is JournalEntryStatus.REVERSED
    assert [(line.debit, line.credit) for line in reinstated.lines] == [
        (line.debit, line.credit) for line in posted.lines
    ]


def test_only_posted_entries_can_be_reversed(books: Session) -> None:
    draft = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    with pytest.raises(EntryStatusError, match="only posted entries"):
        reverse_journal_entry(books, draft, entry_date=MARCH)

    void_journal_entry(draft)
    with pytest.raises(EntryStatusError, match="only posted entries"):
        reverse_journal_entry(books, draft, entry_date=MARCH)


def test_a_rejected_reversal_changes_nothing(
    books: Session, posted: JournalEntry
) -> None:
    close_period(get_period(books, "2026-01"))

    with pytest.raises(JournalEntryError) as caught:
        reverse_journal_entry(books, posted, entry_date=date(2026, 1, 31))
    books.commit()

    assert codes(caught.value.issues) == ["PERIOD_CLOSED"]
    assert posted.status is JournalEntryStatus.POSTED
    assert posted.reversed_by is None
    assert len(books.scalars(select(JournalEntry)).all()) == 1


# --- Database constraints ---------------------------------------------------------
# Raw SQL, because the ORM would refuse these writes before the database saw them.


def test_the_database_rejects_a_line_with_two_sides(books: Session) -> None:
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    books.commit()

    with pytest.raises(IntegrityError):
        books.execute(
            text("UPDATE journal_line SET credit = 500 WHERE id = :id"),
            {"id": entry.lines[0].id},
        )


def test_the_database_rejects_a_value_filed_under_another_dimension(
    books: Session,
) -> None:
    entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
    books.commit()
    engineering = get_dimension_value(books, "DEPARTMENT", "ENG")
    location = get_dimension_value(books, "LOCATION", "HQ").dimension

    with pytest.raises(IntegrityError):
        books.execute(
            text(
                "INSERT INTO journal_line_dimension "
                "(journal_line_id, dimension_id, dimension_value_id) "
                "VALUES (:line, :dimension, :value)"
            ),
            {
                "line": entry.lines[0].id,
                "dimension": location.id,
                "value": engineering.id,
            },
        )


def test_a_reversal_names_an_entry_that_was_never_flushed(books: Session) -> None:
    with books.no_autoflush:
        entry = record(books, debit("6100", "5.00"), credit("1111", "5.00"))
        post_journal_entry(books, entry)
        reversal = reverse_journal_entry(books, entry, entry_date=MARCH)

    assert entry.id is not None
    assert reversal.description == f"Reversal of journal entry {entry.id}"

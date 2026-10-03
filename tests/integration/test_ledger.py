from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from opensumma.kernel.accounts import deactivate_account, get_account
from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.errors import (
    ImmutableEntryError,
    JournalEntryError,
    UnknownAccountError,
)
from opensumma.kernel.journal import (
    LineInput,
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    void_journal_entry,
)
from opensumma.kernel.ledger import (
    LedgerLine,
    account_balance,
    activity_before,
    ledger_lines,
    posted_activity,
)
from opensumma.kernel.models import JournalEntry


def dr(account: str, amount: str, **dimensions: str) -> LineInput:
    return LineInput(account, Decimal(amount), dimensions=dimensions)


def cr(account: str, amount: str, **dimensions: str) -> LineInput:
    return LineInput(account, -Decimal(amount), dimensions=dimensions)


def record(session: Session, on: date, *lines: LineInput) -> JournalEntry:
    return create_journal_entry(
        session, entry_date=on, description=f"Entry on {on}", lines=list(lines)
    )


def post(session: Session, on: date, *lines: LineInput) -> JournalEntry:
    entry = record(session, on, *lines)
    post_journal_entry(session, entry)
    return entry


def money(value: str) -> Decimal:
    return Decimal(value)


def test_only_posted_entries_are_in_the_ledger(books: Session) -> None:
    post(books, date(2026, 1, 5), dr("6100", "100.00"), cr("1111", "100.00"))
    record(books, date(2026, 1, 6), dr("6200", "200.00"), cr("1111", "200.00"))
    void_journal_entry(
        record(books, date(2026, 1, 7), dr("6300", "300.00"), cr("1111", "300.00"))
    )
    unbalanced = record(
        books, date(2026, 1, 8), dr("6400", "400.00"), cr("1111", "1.00")
    )
    with pytest.raises(JournalEntryError):
        post_journal_entry(books, unbalanced)

    assert posted_activity(books) == {
        "1111": money("-100.00"),
        "6100": money("100.00"),
    }


def test_a_reversed_entry_and_its_reversal_both_stay_in_the_ledger(
    books: Session,
) -> None:
    entry = post(books, date(2026, 1, 5), dr("6100", "100.00"), cr("1111", "100.00"))
    reverse_journal_entry(books, entry, entry_date=date(2026, 1, 6))

    assert posted_activity(books) == {"1111": money("0.00"), "6100": money("0.00")}
    assert [(line.account_code, str(line.amount)) for line in ledger_lines(books)] == [
        ("6100", "100.00"),
        ("1111", "-100.00"),
        ("6100", "-100.00"),
        ("1111", "100.00"),
    ]


def test_date_bounds_are_inclusive(books: Session) -> None:
    for day in (1, 15, 31):
        post(
            books, date(2026, 1, day), dr("6100", f"{day}.00"), cr("1111", f"{day}.00")
        )

    assert posted_activity(books, start=date(2026, 1, 15), end=date(2026, 1, 31))[
        "6100"
    ] == money("46.00")
    assert posted_activity(books, end=date(2026, 1, 15))["1111"] == money("-16.00")
    assert activity_before(books, date(2026, 1, 15))["6100"] == money("1.00")


def test_a_date_range_must_run_forwards_and_use_dates(books: Session) -> None:
    with pytest.raises(ValueError):
        posted_activity(books, start=date(2026, 2, 1), end=date(2026, 1, 1))
    with pytest.raises(TypeError):
        ledger_lines(books, end=datetime(2026, 1, 31, tzinfo=UTC))


def test_a_balance_is_positive_for_a_debit_and_negative_for_a_credit(
    books: Session,
) -> None:
    post(books, date(2026, 1, 5), dr("1111", "1000.00"), cr("3100", "1000.00"))
    post(books, date(2026, 1, 6), dr("6600", "40.00"), cr("1590", "40.00"))

    bank = account_balance(books, "1111")
    assert bank.balance == money("1000.00")
    assert bank.normal_balance is NormalBalance.DEBIT

    stock = account_balance(books, "3100")
    assert stock.balance == money("-1000.00")
    assert stock.account_type is AccountType.EQUITY

    depreciation = account_balance(books, "1590")
    assert depreciation.normal_balance is NormalBalance.CREDIT
    assert depreciation.balance == money("-40.00")


def test_a_parent_balance_is_the_sum_of_the_accounts_below_it(books: Session) -> None:
    post(books, date(2026, 1, 5), dr("1111", "900.00"), cr("3100", "900.00"))
    post(books, date(2026, 1, 5), dr("1112", "100.00"), cr("3100", "100.00"))
    post(books, date(2026, 1, 6), dr("1510", "500.00"), cr("1111", "500.00"))
    post(books, date(2026, 1, 7), dr("6600", "50.00"), cr("1590", "50.00"))

    assert account_balance(books, "1110").balance == money("500.00")
    assert account_balance(books, "1500").balance == money("450.00")  # net of 1590
    assert account_balance(books, "1000").balance == money("950.00")
    assert account_balance(books, "3000").balance == money("-1000.00")


def test_a_balance_can_be_taken_as_of_a_date(books: Session) -> None:
    post(books, date(2026, 1, 5), dr("1111", "100.00"), cr("3100", "100.00"))
    post(books, date(2026, 2, 5), dr("1111", "50.00"), cr("3100", "50.00"))

    assert account_balance(books, "1111", as_of=date(2026, 1, 31)).balance == money(
        "100.00"
    )
    assert account_balance(books, "1111").balance == money("150.00")
    assert account_balance(books, "1111", as_of=date(2025, 12, 31)).balance == money(
        "0.00"
    )


def test_an_account_retired_after_posting_keeps_its_balance(books: Session) -> None:
    post(books, date(2026, 1, 5), dr("6900", "12.00"), cr("1111", "12.00"))
    deactivate_account(get_account(books, "6900"))

    assert account_balance(books, "6900").balance == money("12.00")


def test_an_unknown_account_has_no_balance(books: Session) -> None:
    with pytest.raises(UnknownAccountError):
        account_balance(books, "9999")


def test_ledger_lines_come_in_date_entry_and_line_order(books: Session) -> None:
    late = post(books, date(2026, 1, 20), dr("6100", "2.00"), cr("1111", "2.00"))
    early = post(
        books,
        date(2026, 1, 10),
        dr("6200", "1.00", DEPARTMENT="GA", LOCATION="HQ"),
        cr("1111", "1.00"),
    )

    lines = ledger_lines(books)

    assert [(line.entry_id, line.line_number) for line in lines] == [
        (early.id, 1),
        (early.id, 2),
        (late.id, 1),
        (late.id, 2),
    ]
    assert lines[0] == LedgerLine(
        entry_id=early.id,
        entry_date=date(2026, 1, 10),
        line_number=1,
        account_code="6200",
        description="Entry on 2026-01-10",
        memo=None,
        amount=money("1.00"),
        dimensions=(("DEPARTMENT", "GA"), ("LOCATION", "HQ")),
    )
    assert [
        line.account_code for line in ledger_lines(books, account_codes=["6100"])
    ] == ["6100"]


def test_the_ledger_only_ever_grows(books: Session) -> None:
    """Every operation leaves the ledger's existing lines exactly as they were."""
    first = post(books, date(2026, 1, 5), dr("6100", "100.00"), cr("1111", "100.00"))
    books.commit()
    snapshots = [ledger_lines(books)]

    def still_holds_everything() -> None:
        before, after = snapshots[-1], ledger_lines(books)
        assert set(before) <= set(after)
        snapshots.append(after)

    post(books, date(2026, 1, 6), dr("6200", "50.00"), cr("1111", "50.00"))
    still_holds_everything()

    reverse_journal_entry(books, first, entry_date=date(2026, 1, 7))
    still_holds_everything()

    record(books, date(2026, 1, 8), dr("6300", "1.00"), cr("1111", "1.00"))
    still_holds_everything()
    assert len(snapshots[-1]) == len(snapshots[-2])  # a draft adds nothing

    books.commit()
    first.lines[0].amount = money("1.00")
    with pytest.raises(ImmutableEntryError):
        books.flush()
    books.rollback()
    still_holds_everything()
    assert len(snapshots[-1]) == 6


def test_activity_can_be_restricted_to_given_entries(books: Session) -> None:
    post(books, date(2026, 1, 5), dr("6100", "100.00"), cr("1111", "100.00"))
    rent = post(books, date(2026, 1, 6), dr("6200", "200.00"), cr("1111", "200.00"))
    draft = record(books, date(2026, 1, 7), dr("6300", "300.00"), cr("1111", "300.00"))
    books.flush()

    assert posted_activity(books, entry_ids=[rent.id, draft.id]) == {
        "6200": money("200.00"),
        "1111": money("-200.00"),
    }
    assert posted_activity(books, entry_ids=[]) == {}

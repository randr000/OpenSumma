from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from opensumma.kernel.accounts import deactivate_account, get_account
from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.errors import UnknownAccountError
from opensumma.kernel.journal import (
    LineInput,
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    void_journal_entry,
)
from opensumma.kernel.models import JournalEntry
from opensumma.kernel.reports import (
    StatementSection,
    TrialBalanceLine,
    balance_sheet,
    general_ledger,
    income_statement,
    trial_balance,
)

JAN_31 = date(2026, 1, 31)


def dr(account: str, amount: str, memo: str | None = None, **dims: str) -> LineInput:
    return LineInput(account, debit=Decimal(amount), memo=memo, dimensions=dims)


def cr(account: str, amount: str, memo: str | None = None, **dims: str) -> LineInput:
    return LineInput(account, credit=Decimal(amount), memo=memo, dimensions=dims)


def record(session: Session, on: date, text: str, *lines: LineInput) -> JournalEntry:
    return create_journal_entry(
        session, entry_date=on, description=text, lines=list(lines)
    )


def post(session: Session, on: date, text: str, *lines: LineInput) -> JournalEntry:
    entry = record(session, on, text, *lines)
    post_journal_entry(session, entry)
    return entry


def money(value: str) -> Decimal:
    return Decimal(value)


def amounts(section: StatementSection) -> list[tuple[str, int, str]]:
    return [(line.account_code, line.depth, str(line.amount)) for line in section.lines]


# --- Trial balance ----------------------------------------------------------------


def test_each_balance_sits_in_the_column_it_falls_in(books: Session) -> None:
    post(
        books, date(2026, 1, 2), "Capital", dr("1111", "1000.00"), cr("3100", "1000.00")
    )
    post(books, date(2026, 1, 9), "AWS", dr("6100", "120.00"), cr("2110", "120.00"))

    report = trial_balance(books, as_of=JAN_31)

    assert [
        (line.account_code, line.account_type, str(line.debit), str(line.credit))
        for line in report.lines
    ] == [
        ("1111", AccountType.ASSET, "1000.00", "0.00"),
        ("2110", AccountType.LIABILITY, "0.00", "120.00"),
        ("3100", AccountType.EQUITY, "0.00", "1000.00"),
        ("6100", AccountType.EXPENSE, "120.00", "0.00"),
    ]
    assert report.lines[0] == TrialBalanceLine(
        "1111",
        "Operating Bank Account",
        AccountType.ASSET,
        money("1000.00"),
        money("0.00"),
    )
    assert report.total_debits == report.total_credits == money("1120.00")
    assert report.is_balanced


def test_a_contra_balance_sits_in_the_opposite_column_to_its_type(
    books: Session,
) -> None:
    post(
        books,
        date(2026, 1, 31),
        "Depreciation",
        dr("6600", "40.00"),
        cr("1590", "40.00"),
    )

    line = next(
        line
        for line in trial_balance(books, as_of=JAN_31).lines
        if line.account_code == "1590"
    )
    assert (line.account_type, line.debit, line.credit) == (
        AccountType.ASSET,
        money("0.00"),
        money("40.00"),
    )


def test_accounts_that_net_to_nothing_are_left_off(books: Session) -> None:
    entry = post(
        books, date(2026, 1, 5), "Misposted", dr("6500", "30.00"), cr("1111", "30.00")
    )
    reverse_journal_entry(books, entry, entry_date=date(2026, 1, 6))

    report = trial_balance(books, as_of=JAN_31)

    assert report.lines == ()
    assert report.total_debits == report.total_credits == money("0.00")


def test_the_trial_balance_is_taken_as_of_a_date(books: Session) -> None:
    post(books, date(2026, 1, 31), "January", dr("6100", "10.00"), cr("1111", "10.00"))
    post(books, date(2026, 2, 1), "February", dr("6100", "99.00"), cr("1111", "99.00"))

    assert trial_balance(books, as_of=JAN_31).total_debits == money("10.00")
    assert trial_balance(books, as_of=date(2025, 12, 31)).lines == ()


def test_an_account_retired_after_posting_still_appears(books: Session) -> None:
    post(books, date(2026, 1, 5), "Fees", dr("6900", "12.00"), cr("1111", "12.00"))
    deactivate_account(get_account(books, "6900"))

    report = trial_balance(books, as_of=JAN_31)

    assert [line.account_code for line in report.lines] == ["1111", "6900"]
    assert report.is_balanced


# --- General ledger ---------------------------------------------------------------


@pytest.fixture
def payables(books: Session) -> Session:
    """Accounts payable across a month boundary, with memos and dimensions."""
    post(
        books,
        date(2026, 1, 9),
        "AWS January",
        dr("6100", "120.00"),
        cr("2110", "120.00"),
    )
    post(books, date(2026, 2, 3), "Pay AWS", dr("2110", "120.00"), cr("1111", "120.00"))
    post(
        books,
        date(2026, 2, 9),
        "AWS February",
        dr("6100", "80.00", "compute", DEPARTMENT="ENG"),
        dr("6100", "20.00", "storage", DEPARTMENT="GA"),
        cr("2110", "100.00"),
    )
    return books


def test_the_general_ledger_carries_a_balance_from_opening_to_closing(
    payables: Session,
) -> None:
    report = general_ledger(
        payables, start=date(2026, 2, 1), end=date(2026, 2, 28), account_code="2110"
    )

    (account,) = report.accounts
    assert (account.account_code, account.normal_balance) == (
        "2110",
        NormalBalance.CREDIT,
    )
    assert account.opening_balance == money("120.00")
    assert [(line.description, str(line.balance)) for line in account.lines] == [
        ("Pay AWS", "0.00"),
        ("AWS February", "100.00"),
    ]
    assert account.closing_balance == money("100.00")


def test_general_ledger_lines_keep_their_memos_and_dimensions(
    payables: Session,
) -> None:
    report = general_ledger(
        payables, start=date(2026, 2, 1), end=date(2026, 2, 28), account_code="6100"
    )

    assert [
        (line.line_number, line.memo, line.dimensions, str(line.balance))
        for line in report.accounts[0].lines
    ] == [
        (1, "compute", (("DEPARTMENT", "ENG"),), "200.00"),
        (2, "storage", (("DEPARTMENT", "GA"),), "220.00"),
    ]


def test_the_general_ledger_includes_accounts_with_only_a_balance_brought_forward(
    payables: Session,
) -> None:
    report = general_ledger(payables, start=date(2026, 2, 10), end=date(2026, 2, 28))

    assert [
        (account.account_code, str(account.opening_balance), len(account.lines))
        for account in report.accounts
    ] == [
        ("1111", "-120.00", 0),
        ("2110", "100.00", 0),
        ("6100", "220.00", 0),
    ]


def test_a_parent_account_expands_to_every_account_below_it(payables: Session) -> None:
    report = general_ledger(
        payables, start=date(2026, 1, 1), end=date(2026, 1, 31), account_code="1110"
    )

    assert [
        (account.account_code, str(account.closing_balance), account.lines)
        for account in report.accounts
    ] == [("1111", "0.00", ()), ("1112", "0.00", ())]


def test_the_general_ledger_rejects_unknown_accounts_and_backwards_ranges(
    books: Session,
) -> None:
    with pytest.raises(UnknownAccountError):
        general_ledger(books, start=date(2026, 1, 1), end=JAN_31, account_code="9999")
    with pytest.raises(ValueError):
        general_ledger(books, start=JAN_31, end=date(2026, 1, 1))


# --- Income statement -------------------------------------------------------------


@pytest.fixture
def trading(books: Session) -> Session:
    """Sales, a return, and costs in January, and a sale in February."""
    post(books, date(2026, 1, 10), "Sale", dr("1120", "1500.00"), cr("4100", "1500.00"))
    post(
        books,
        date(2026, 1, 12),
        "Consulting",
        dr("1111", "500.00"),
        cr("4200", "500.00"),
    )
    post(books, date(2026, 1, 25), "Return", dr("4900", "100.00"), cr("1120", "100.00"))
    post(
        books,
        date(2026, 1, 28),
        "Goods sold",
        dr("5100", "600.00"),
        cr("1140", "600.00"),
    )
    post(books, date(2026, 1, 31), "Rent", dr("6200", "250.00"), cr("1111", "250.00"))
    post(books, date(2026, 2, 10), "Sale", dr("1120", "700.00"), cr("4100", "700.00"))
    return books


def test_the_income_statement_rolls_revenue_and_expenses_up_the_chart(
    trading: Session,
) -> None:
    report = income_statement(trading, start=date(2026, 1, 1), end=JAN_31)

    assert amounts(report.revenue) == [
        ("4000", 0, "1900.00"),
        ("4100", 1, "1500.00"),
        ("4200", 1, "500.00"),
        ("4900", 1, "-100.00"),
    ]
    assert amounts(report.expenses) == [
        ("5000", 0, "600.00"),
        ("5100", 1, "600.00"),
        ("6000", 0, "250.00"),
        ("6200", 1, "250.00"),
    ]
    assert (report.revenue.total, report.expenses.total, report.net_income) == (
        money("1900.00"),
        money("850.00"),
        money("1050.00"),
    )
    assert [line.is_leaf for line in report.revenue.lines] == [False, True, True, True]


def test_the_income_statement_covers_only_its_date_range(trading: Session) -> None:
    february = income_statement(trading, start=date(2026, 2, 1), end=date(2026, 2, 28))

    assert amounts(february.revenue) == [("4000", 0, "700.00"), ("4100", 1, "700.00")]
    assert february.expenses.lines == ()
    assert february.net_income == money("700.00")


def test_an_empty_period_earns_nothing(books: Session) -> None:
    report = income_statement(books, start=date(2026, 3, 1), end=date(2026, 3, 31))

    assert report.revenue.lines == report.expenses.lines == ()
    assert report.net_income == money("0.00")


# --- Balance sheet ----------------------------------------------------------------


def test_the_balance_sheet_balances_with_unclosed_net_income_in_equity(
    trading: Session,
) -> None:
    post(
        trading,
        date(2026, 1, 2),
        "Capital",
        dr("1111", "5000.00"),
        cr("3100", "5000.00"),
    )
    post(
        trading, date(2026, 1, 3), "Stock", dr("1140", "2000.00"), cr("2110", "2000.00")
    )
    post(
        trading,
        date(2026, 1, 31),
        "Depreciation",
        dr("6600", "50.00"),
        cr("1590", "50.00"),
    )

    report = balance_sheet(trading, as_of=JAN_31)

    assert amounts(report.assets) == [
        ("1000", 0, "8000.00"),
        ("1100", 1, "8050.00"),
        ("1110", 2, "5250.00"),
        ("1111", 3, "5250.00"),
        ("1120", 2, "1400.00"),
        ("1140", 2, "1400.00"),
        ("1500", 1, "-50.00"),
        ("1590", 2, "-50.00"),
    ]
    assert amounts(report.liabilities) == [
        ("2000", 0, "2000.00"),
        ("2100", 1, "2000.00"),
        ("2110", 2, "2000.00"),
    ]
    assert amounts(report.equity) == [("3000", 0, "5000.00"), ("3100", 1, "5000.00")]
    assert report.unclosed_net_income == money("1000.00")
    assert report.total_equity == money("6000.00")
    assert (
        report.assets.total == report.total_liabilities_and_equity == money("8000.00")
    )
    assert report.is_balanced


def test_unclosed_net_income_is_everything_earned_up_to_the_date(
    trading: Session,
) -> None:
    january = income_statement(trading, start=date(2026, 1, 1), end=JAN_31)
    february = income_statement(trading, start=date(2026, 2, 1), end=date(2026, 2, 28))

    assert balance_sheet(trading, as_of=date(2026, 2, 28)).unclosed_net_income == (
        january.net_income + february.net_income
    )


def test_drafts_and_voided_entries_never_reach_a_report(books: Session) -> None:
    record(books, date(2026, 1, 5), "Draft", dr("6100", "10.00"), cr("1111", "10.00"))
    void_journal_entry(
        record(
            books, date(2026, 1, 6), "Voided", dr("4100", "20.00"), cr("1120", "20.00")
        )
    )

    assert trial_balance(books, as_of=JAN_31).lines == ()
    assert income_statement(books, start=date(2026, 1, 1), end=JAN_31).net_income == (
        money("0.00")
    )
    sheet = balance_sheet(books, as_of=JAN_31)
    assert sheet.assets.lines == sheet.liabilities.lines == sheet.equity.lines == ()
    assert general_ledger(books, start=date(2026, 1, 1), end=JAN_31).accounts == ()

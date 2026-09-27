"""Phase 3 acceptance: the immutable ledger and the financial reports derived from it.

A small company's January is posted through the public kernel API, on a database
created by the migrations. Every expected figure below was worked out by hand from
these transactions, not taken from the code under test:

    Jan  2  Owner invests 100,000 cash          Dr 1111  Cr 3100   100,000.00
    Jan  5  Buys equipment for cash             Dr 1510  Cr 1111    24,000.00
    Jan 10  Sells goods on credit               Dr 1120  Cr 4100    15,000.00
    Jan 15  Consulting paid in cash             Dr 1111  Cr 4200     5,000.00
    Jan 20  Customer pays part of the invoice   Dr 1111  Cr 1120    10,000.00
    Jan 25  Customer returns goods              Dr 4900  Cr 1120     1,000.00
    Jan 28  T&E posted in error ...             Dr 6500  Cr 1111       300.00
    Jan 29  ... and reversed                    Dr 1111  Cr 6500       300.00
    Jan 31  Rent                                Dr 6200  Cr 1111     2,500.00
    Jan 31  AWS bill, not yet paid              Dr 6100  Cr 2110     1,200.00
    Jan 31  Depreciation                        Dr 6600  Cr 1590       400.00
    Jan 31  Payroll: 6,500 paid, 1,500 withheld Dr 6300  Cr 1111/2130 8,000.00

Also recorded but never posted: a draft (6700, 999.00) and a voided entry
(6800, 777.00). Neither may appear in any report.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from opensumma.db import init_db
from opensumma.kernel import (
    ImmutableEntryError,
    JournalEntry,
    LineInput,
    StatementSection,
    account_balance,
    balance_sheet,
    create_calendar_year_periods,
    create_journal_entry,
    general_ledger,
    income_statement,
    ledger_lines,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    seed_dimensions,
    trial_balance,
    void_journal_entry,
)

JAN_1, JAN_31 = date(2026, 1, 1), date(2026, 1, 31)


def _entry(
    session: Session,
    day: int,
    text: str,
    debits: dict[str, str],
    credits: dict[str, str],
) -> JournalEntry:
    return create_journal_entry(
        session,
        entry_date=date(2026, 1, day),
        description=text,
        lines=[
            *(
                LineInput(code, debit=Decimal(amount))
                for code, amount in debits.items()
            ),
            *(
                LineInput(code, credit=Decimal(amount))
                for code, amount in credits.items()
            ),
        ],
    )


@pytest.fixture
def january(database_url: str, engine: Engine) -> Iterator[Session]:
    init_db(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        create_calendar_year_periods(session, 2026)

        postings = [
            (2, "Owner investment", {"1111": "100000.00"}, {"3100": "100000.00"}),
            (5, "Equipment", {"1510": "24000.00"}, {"1111": "24000.00"}),
            (10, "Sale on credit", {"1120": "15000.00"}, {"4100": "15000.00"}),
            (15, "Consulting", {"1111": "5000.00"}, {"4200": "5000.00"}),
            (20, "Customer payment", {"1111": "10000.00"}, {"1120": "10000.00"}),
            (25, "Sales return", {"4900": "1000.00"}, {"1120": "1000.00"}),
            (31, "Rent", {"6200": "2500.00"}, {"1111": "2500.00"}),
            (31, "AWS", {"6100": "1200.00"}, {"2110": "1200.00"}),
            (31, "Depreciation", {"6600": "400.00"}, {"1590": "400.00"}),
            (
                31,
                "Payroll",
                {"6300": "8000.00"},
                {"1111": "6500.00", "2130": "1500.00"},
            ),
        ]
        for posting in postings:
            post_journal_entry(session, _entry(session, *posting))

        error = _entry(
            session, 28, "Misposted T&E", {"6500": "300.00"}, {"1111": "300.00"}
        )
        post_journal_entry(session, error)
        reverse_journal_entry(session, error, entry_date=date(2026, 1, 29))

        _entry(session, 30, "Draft", {"6700": "999.00"}, {"1111": "999.00"})
        void_journal_entry(
            _entry(session, 30, "Voided", {"6800": "777.00"}, {"1111": "777.00"})
        )
        session.commit()
        yield session


def _amounts(section: StatementSection) -> dict[str, str]:
    return {line.account_code: str(line.amount) for line in section.lines}


def test_the_ledger_is_immutable_and_append_only(january: Session) -> None:
    lines = ledger_lines(january)
    assert len(lines) == 25  # 12 posted two- or three-line entries; no draft or void
    assert {line.account_code for line in lines}.isdisjoint({"6700", "6800"})

    rent = next(line for line in lines if line.description == "Rent")
    entry = january.get(JournalEntry, rent.entry_id)
    assert entry is not None
    entry.lines[0].debit = Decimal("1.00")
    with pytest.raises(ImmutableEntryError):
        january.commit()
    january.rollback()

    assert ledger_lines(january) == lines


def test_trial_balance_works(january: Session) -> None:
    report = trial_balance(january, as_of=JAN_31)

    assert [
        (line.account_code, str(line.debit), str(line.credit)) for line in report.lines
    ] == [
        ("1111", "82000.00", "0.00"),
        ("1120", "4000.00", "0.00"),
        ("1510", "24000.00", "0.00"),
        ("1590", "0.00", "400.00"),
        ("2110", "0.00", "1200.00"),
        ("2130", "0.00", "1500.00"),
        ("3100", "0.00", "100000.00"),
        ("4100", "0.00", "15000.00"),
        ("4200", "0.00", "5000.00"),
        ("4900", "1000.00", "0.00"),
        ("6100", "1200.00", "0.00"),
        ("6200", "2500.00", "0.00"),
        ("6300", "8000.00", "0.00"),
        ("6600", "400.00", "0.00"),
    ]  # 6500 is absent: the misposting and its reversal cancel out
    assert report.total_debits == report.total_credits == Decimal("123100.00")
    assert report.is_balanced


def test_general_ledger_works(january: Session) -> None:
    report = general_ledger(
        january, start=date(2026, 1, 20), end=JAN_31, account_code="1111"
    )

    (bank,) = report.accounts
    assert bank.opening_balance == Decimal("81000.00")  # 100,000 - 24,000 + 5,000
    assert [
        (line.description, str(line.debit), str(line.credit), str(line.balance))
        for line in bank.lines
    ] == [
        ("Customer payment", "10000.00", "0.00", "91000.00"),
        ("Misposted T&E", "0.00", "300.00", "90700.00"),
        ("Reversal of journal entry 11", "300.00", "0.00", "91000.00"),
        ("Rent", "0.00", "2500.00", "88500.00"),
        ("Payroll", "0.00", "6500.00", "82000.00"),
    ]
    assert bank.closing_balance == Decimal("82000.00")


def test_income_statement_works(january: Session) -> None:
    report = income_statement(january, start=JAN_1, end=JAN_31)

    assert _amounts(report.revenue) == {
        "4000": "19000.00",
        "4100": "15000.00",
        "4200": "5000.00",
        "4900": "-1000.00",  # sales returns reduce revenue
    }
    assert _amounts(report.expenses) == {
        "6000": "12100.00",
        "6100": "1200.00",
        "6200": "2500.00",
        "6300": "8000.00",
        "6600": "400.00",
    }
    assert report.net_income == Decimal("6900.00")


def test_balance_sheet_works(january: Session) -> None:
    report = balance_sheet(january, as_of=JAN_31)

    assert _amounts(report.assets) == {
        "1000": "109600.00",
        "1100": "86000.00",
        "1110": "82000.00",
        "1111": "82000.00",
        "1120": "4000.00",
        "1500": "23600.00",
        "1510": "24000.00",
        "1590": "-400.00",  # accumulated depreciation reduces fixed assets
    }
    assert _amounts(report.liabilities) == {
        "2000": "2700.00",
        "2100": "2700.00",
        "2110": "1200.00",
        "2130": "1500.00",
    }
    assert _amounts(report.equity) == {"3000": "100000.00", "3100": "100000.00"}
    assert report.unclosed_net_income == Decimal("6900.00")
    assert report.total_equity == Decimal("106900.00")


def test_the_accounting_equation_holds(january: Session) -> None:
    february = create_journal_entry(
        january,
        entry_date=date(2026, 2, 3),
        description="Pay AWS",
        lines=[
            LineInput("2110", debit=Decimal("1200.00")),
            LineInput("1111", credit=Decimal("1200.00")),
        ],
    )
    post_journal_entry(january, february)
    january.commit()

    for as_of in (date(2026, 1, 1), date(2026, 1, 15), JAN_31, date(2026, 2, 28)):
        sheet = balance_sheet(january, as_of=as_of)
        assert sheet.is_balanced, as_of
        assert sheet.assets.total == (
            sheet.liabilities.total + sheet.equity.total + sheet.unclosed_net_income
        )

    end_of_february = balance_sheet(january, as_of=date(2026, 2, 28))
    assert end_of_february.assets.total == Decimal("108400.00")
    assert end_of_february.liabilities.total == Decimal("1500.00")


def test_financial_reports_derive_from_the_posted_ledger(january: Session) -> None:
    before = (
        trial_balance(january, as_of=JAN_31),
        income_statement(january, start=JAN_1, end=JAN_31),
        balance_sheet(january, as_of=JAN_31),
    )

    draft = _entry(january, 31, "Accrued bonus", {"6300": "500.00"}, {"2120": "500.00"})
    january.commit()
    assert (
        trial_balance(january, as_of=JAN_31),
        income_statement(january, start=JAN_1, end=JAN_31),
        balance_sheet(january, as_of=JAN_31),
    ) == before

    post_journal_entry(january, draft)
    january.commit()
    assert income_statement(january, start=JAN_1, end=JAN_31).net_income == Decimal(
        "6400.00"
    )
    assert account_balance(january, "2120", as_of=JAN_31).balance == Decimal("500.00")

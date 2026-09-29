"""Phase 4 acceptance: Accounting Objects, business events, and accounting impact.

A small company's March is recorded through the public API on a database created
by the migrations. Business documents arrive as Accounting Objects; their
accounting impact is recorded only through journal entries the kernel validates and
posts. Every expected figure below was worked out by hand:

    Mar  1  Owner invests 50,000 (no object)          Dr 1111  Cr 3100  50,000.00
    Mar  5  Stratus bill INV-1001 posted to the       Dr 6100  Cr 2110   1,200.00
            wrong account ...
    Mar  7  ... reversed ...                          Dr 2110  Cr 6100   1,200.00
    Mar  7  ... and posted to hosting                 Dr 5200  Cr 2110   1,200.00
    Mar 10  Invoice INV-C-1 to Bluefin                Dr 1120  Cr 4200   5,000.00
    Mar 20  Stratus paid; records payment and bill    Dr 2110  Cr 1111   1,200.00
    Mar 31  Accrual for goods received on PO-77       Dr 6700  Cr 2120     450.00

Also observed but never in the ledger: a duplicate of INV-1001 arriving through the
vendor portal (voided), and an invoice to Cedar & Pine whose proposed entry does not
balance and credits a parent account (it cannot post).

Trial balance at Mar 31: 1111 48,800 Dr; 1120 5,000 Dr; 2120 450 Cr; 3100 50,000
Cr; 4200 5,000 Cr; 5200 1,200 Dr; 6700 450 Dr. Totals 55,450. 2110 and 6100 net to
nothing. Net income: 5,000 - 1,200 - 450 = 3,350.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from opensumma.db import init_db
from opensumma.kernel import (
    Activity,
    IssueCode,
    JournalEntry,
    JournalEntryError,
    JournalEntryStatus,
    LineInput,
    balance_sheet,
    create_calendar_year_periods,
    create_journal_entry,
    income_statement,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    seed_dimensions,
    trial_balance,
    void_journal_entry,
)
from opensumma.objects import (
    AccountingObject,
    AccountingObjectStatus,
    AccountingObjectType,
    ImmutableRecordError,
    ObjectHasAccountingImpactError,
    VoidedObjectError,
    accounting_impact,
    create_accounting_object,
    create_journal_entry_for_object,
    get_accounting_object,
    link_journal_entry,
    objects_for_journal_entry,
    record_accounting_event,
    search_accounting_objects,
    seed_counterparties,
    void_accounting_object,
)

MAR_1, MAR_31 = date(2026, 3, 1), date(2026, 3, 31)


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 3, day, hour, tzinfo=UTC)


def _lines(debits: dict[str, str], credits: dict[str, str]) -> list[LineInput]:
    return [
        *(LineInput(code, debit=Decimal(amount)) for code, amount in debits.items()),
        *(LineInput(code, credit=Decimal(amount)) for code, amount in credits.items()),
    ]


@dataclass
class March:
    session: Session
    bill: AccountingObject
    duplicate: AccountingObject
    payment: AccountingObject
    bluefin: AccountingObject
    cedar: AccountingObject
    order: AccountingObject
    misposted: JournalEntry
    reversal: JournalEntry
    corrected: JournalEntry
    paid: JournalEntry
    proposal: JournalEntry


@pytest.fixture
def march(database_url: str, engine: Engine) -> Iterator[March]:
    init_db(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        seed_counterparties(session)
        create_calendar_year_periods(session, 2026)

        capital = create_journal_entry(
            session,
            entry_date=MAR_1,
            description="Owner investment",
            lines=_lines({"1111": "50000.00"}, {"3100": "50000.00"}),
        )
        post_journal_entry(session, capital)

        bill = create_accounting_object(
            session,
            object_type=AccountingObjectType.VENDOR_BILL,
            occurred_at=_at(5),
            source="email",
            counterparty="V-STRATUS",
            data={
                "invoice_number": "INV-1001",
                "amount": "1200.00",
                "lines": [{"description": "EC2, March", "amount": "1200.00"}],
            },
        )
        misposted = create_journal_entry_for_object(
            session,
            bill,
            entry_date=date(2026, 3, 5),
            description="Stratus INV-1001",
            lines=_lines({"6100": "1200.00"}, {"2110": "1200.00"}),
        )
        post_journal_entry(session, misposted)
        reversal = reverse_journal_entry(
            session, misposted, entry_date=date(2026, 3, 7)
        )
        corrected = create_journal_entry_for_object(
            session,
            bill,
            entry_date=date(2026, 3, 7),
            description="Stratus INV-1001, hosting",
            lines=_lines({"5200": "1200.00"}, {"2110": "1200.00"}),
        )
        post_journal_entry(session, corrected)

        duplicate = create_accounting_object(
            session,
            object_type="vendor_bill",
            occurred_at=_at(6),
            source="vendor_portal",
            counterparty="V-STRATUS",
            data={"invoice_number": "INV-1001", "amount": "1200.00"},
        )

        bluefin = create_accounting_object(
            session,
            object_type="customer_invoice",
            occurred_at=_at(10),
            source="billing",
            counterparty="C-BLUEFIN",
            data={"invoice_number": "INV-C-1", "amount": "5000.00"},
        )
        post_journal_entry(
            session,
            create_journal_entry_for_object(
                session,
                bluefin,
                entry_date=date(2026, 3, 10),
                description="Consulting for Bluefin",
                lines=_lines({"1120": "5000.00"}, {"4200": "5000.00"}),
            ),
        )

        cedar = create_accounting_object(
            session,
            object_type="customer_invoice",
            occurred_at=_at(12),
            source="billing",
            counterparty="C-CEDAR",
            data={"invoice_number": "INV-C-2", "amount": "800.00"},
        )
        proposal = create_journal_entry_for_object(
            session,
            cedar,
            entry_date=date(2026, 3, 12),
            description="Consulting for Cedar & Pine",
            lines=_lines({"1120": "800.00"}, {"4000": "750.00"}),
        )

        payment = create_accounting_object(
            session,
            object_type="vendor_payment",
            occurred_at=_at(20),
            source="bank_feed",
            counterparty="V-STRATUS",
            data={"reference": "ACH-5521", "amount": "1200.00"},
        )
        paid = create_journal_entry_for_object(
            session,
            payment,
            entry_date=date(2026, 3, 20),
            description="Pay Stratus INV-1001",
            lines=_lines({"2110": "1200.00"}, {"1111": "1200.00"}),
        )
        link_journal_entry(session, bill, paid)
        post_journal_entry(session, paid)

        order = create_accounting_object(
            session,
            object_type="purchase_order",
            occurred_at=_at(1),
            source="procurement",
            counterparty="V-PAPER",
            data={"po_number": "PO-77", "amount": "450.00"},
        )
        record_accounting_event(
            session,
            order,
            event_type="issued",
            occurred_at=_at(1),
            source="procurement",
        )
        record_accounting_event(
            session,
            order,
            event_type="goods_received",
            occurred_at=_at(28, 15),
            source="warehouse",
            data={"receipt": "GR-7", "quantity": 30},
        )
        post_journal_entry(
            session,
            create_journal_entry_for_object(
                session,
                order,
                entry_date=MAR_31,
                description="Accrue PO-77 goods received, not yet billed",
                lines=_lines({"6700": "450.00"}, {"2120": "450.00"}),
            ),
        )
        session.commit()

        yield March(
            session=session,
            bill=bill,
            duplicate=duplicate,
            payment=payment,
            bluefin=bluefin,
            cedar=cedar,
            order=order,
            misposted=misposted,
            reversal=reversal,
            corrected=corrected,
            paid=paid,
            proposal=proposal,
        )


def test_accounting_objects_exist(march: March) -> None:
    march.session.expire_all()
    bill = get_accounting_object(march.session, march.bill.id)

    assert bill.object_type is AccountingObjectType.VENDOR_BILL
    assert bill.status is AccountingObjectStatus.OBSERVED
    assert bill.occurred_at == _at(5)
    assert bill.source == "email"
    assert bill.counterparty is not None
    assert bill.counterparty.code == "V-STRATUS"
    assert bill.created_at.tzinfo is UTC and bill.updated_at.tzinfo is UTC
    observed = search_accounting_objects(march.session)  # by occurrence
    assert observed == [
        march.order,  # Mar 1
        march.bill,  # Mar 5
        march.duplicate,  # Mar 6
        march.bluefin,  # Mar 10
        march.cedar,  # Mar 12
        march.payment,  # Mar 20
    ]
    assert [obj.object_type.value for obj in observed] == [
        "purchase_order",
        "vendor_bill",
        "vendor_bill",
        "customer_invoice",
        "customer_invoice",
        "vendor_payment",
    ]


def test_json_business_data_is_supported(march: March) -> None:
    march.session.expire_all()
    bill = get_accounting_object(march.session, march.bill.id)
    assert bill.data == {
        "invoice_number": "INV-1001",
        "amount": "1200.00",
        "lines": [{"description": "EC2, March", "amount": "1200.00"}],
    }

    # Business data is queryable: the duplicate invoice is found by its number.
    assert search_accounting_objects(
        march.session,
        object_type="vendor_bill",
        counterparty="V-STRATUS",
        data={"invoice_number": "INV-1001"},
    ) == [march.bill, march.duplicate]

    # Floats never reach business data; amounts are strings.
    bill.data = {"amount": 1200.0}
    with pytest.raises(StatementError, match="write amounts as strings"):
        march.session.flush()


def test_business_events_are_supported(march: March) -> None:
    march.session.expire_all()
    order = get_accounting_object(march.session, march.order.id)

    assert [
        (event.event_type, event.occurred_at, event.source, event.data)
        for event in order.events
    ] == [
        ("issued", _at(1), "procurement", {}),
        (
            "goods_received",
            _at(28, 15),
            "warehouse",
            {"receipt": "GR-7", "quantity": 30},
        ),
    ]

    order.events[0].event_type = "cancelled"
    with pytest.raises(ImmutableRecordError):
        march.session.flush()


def test_objects_are_related_to_their_accounting_impact(march: March) -> None:
    impact = accounting_impact(march.session, march.bill)

    assert [
        (entry.entry_id, entry.status, entry.linked) for entry in impact.entries
    ] == [
        (march.misposted.id, JournalEntryStatus.REVERSED, True),
        (march.reversal.id, JournalEntryStatus.POSTED, False),
        (march.corrected.id, JournalEntryStatus.POSTED, True),
        (march.paid.id, JournalEntryStatus.POSTED, True),
    ]
    assert impact.activity == {
        "6100": Activity(Decimal("1200.00"), Decimal("1200.00")),
        "2110": Activity(Decimal("2400.00"), Decimal("2400.00")),
        "5200": Activity(debits=Decimal("1200.00")),
        "1111": Activity(credits=Decimal("1200.00")),
    }  # the bill ended up as hosting expense, paid from the bank
    assert impact.has_net_impact

    assert objects_for_journal_entry(march.session, march.paid) == [
        march.bill,
        march.payment,
    ]
    assert accounting_impact(march.session, march.order).activity == {
        "6700": Activity(debits=Decimal("450.00")),
        "2120": Activity(credits=Decimal("450.00")),
    }
    assert accounting_impact(march.session, march.duplicate).entries == ()


def test_objects_cannot_bypass_accounting_validation(march: March) -> None:
    session = march.session

    # A proposal made for an object is validated by the kernel like any other.
    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(session, march.proposal)
    assert [issue.code for issue in caught.value.issues] == [
        IssueCode.UNBALANCED,
        IssueCode.ACCOUNT_NOT_POSTABLE,
    ]

    # An object the ledger still carries cannot be voided away.
    with pytest.raises(ObjectHasAccountingImpactError):
        void_accounting_object(session, march.bill)
    with pytest.raises(ObjectHasAccountingImpactError):
        void_accounting_object(session, march.cedar)  # its draft could still post

    # The duplicate never reached the ledger, so it can be withdrawn; once voided,
    # nothing can record it.
    void_accounting_object(session, march.duplicate)
    with pytest.raises(VoidedObjectError):
        create_journal_entry_for_object(
            session,
            march.duplicate,
            entry_date=MAR_31,
            description="Second copy of INV-1001",
            lines=_lines({"5200": "1200.00"}, {"2110": "1200.00"}),
        )

    # Abandoning the bad proposal lets the Cedar & Pine invoice be withdrawn too.
    void_journal_entry(march.proposal)
    void_accounting_object(session, march.cedar)
    session.commit()

    # Objects are voided, never deleted.
    session.delete(march.duplicate)
    with pytest.raises(ImmutableRecordError):
        session.flush()


def test_reports_derive_from_the_ledger_not_from_objects(march: March) -> None:
    session = march.session
    # The bills' business data claims 2,400.00, because INV-1001 arrived twice;
    # the ledger recorded the expense once.
    bills = search_accounting_objects(session, object_type="vendor_bill")
    assert sum(Decimal(bill.data["amount"]) for bill in bills) == Decimal("2400.00")

    report = trial_balance(session, as_of=MAR_31)
    assert [
        (line.account_code, str(line.debit), str(line.credit)) for line in report.lines
    ] == [
        ("1111", "48800.00", "0.00"),
        ("1120", "5000.00", "0.00"),  # the unposted Cedar & Pine proposal is absent
        ("2120", "0.00", "450.00"),
        ("3100", "0.00", "50000.00"),
        ("4200", "0.00", "5000.00"),
        ("5200", "1200.00", "0.00"),
        ("6700", "450.00", "0.00"),
    ]  # 2110 and 6100 net to nothing
    assert report.total_debits == report.total_credits == Decimal("55450.00")

    assert income_statement(session, start=MAR_1, end=MAR_31).net_income == Decimal(
        "3350.00"
    )
    sheet = balance_sheet(session, as_of=MAR_31)
    assert sheet.assets.total == Decimal("53800.00")
    assert sheet.liabilities.total == Decimal("450.00")
    assert sheet.unclosed_net_income == Decimal("3350.00")
    assert sheet.total_liabilities_and_equity == Decimal("53800.00")
    assert sheet.is_balanced

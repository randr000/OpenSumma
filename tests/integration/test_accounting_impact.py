"""How accounting objects relate to the ledger: only through journal entries.

An object's accounting impact is what the journal entries linked to it, and every
reversal of them, have posted. Those entries are recorded, validated, posted, and
reversed by the kernel; nothing here lets an object reach the ledger another way.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opensumma.kernel import (
    Activity,
    EntryStatusError,
    IssueCode,
    JournalEntry,
    JournalEntryError,
    JournalEntryStatus,
    LineInput,
    close_period,
    create_journal_entry,
    get_period,
    ledger_lines,
    post_journal_entry,
    reverse_journal_entry,
    trial_balance,
    void_journal_entry,
)
from opensumma.objects import (
    AccountingObject,
    AccountingObjectEntry,
    AccountingObjectStatus,
    AlreadyLinkedError,
    ImmutableRecordError,
    ObjectHasAccountingImpactError,
    VoidedObjectError,
    accounting_impact,
    create_accounting_object,
    create_journal_entry_for_object,
    link_journal_entry,
    objects_for_journal_entry,
    record_accounting_event,
    void_accounting_object,
)

MARCH_15 = date(2026, 3, 15)
RECEIVED = datetime(2026, 3, 15, 9, 30, tzinfo=UTC)
AWS_BILL = (
    LineInput("6100", debit=Decimal("1200.00")),
    LineInput("2110", credit=Decimal("1200.00")),
)


def _bill(session: Session, invoice: str = "INV-1001") -> AccountingObject:
    return create_accounting_object(
        session,
        object_type="vendor_bill",
        occurred_at=RECEIVED,
        source="email",
        entity_id="V-AWS",
        data={"invoice_number": invoice, "amount": "1200.00"},
    )


def _record(
    session: Session,
    obj: AccountingObject,
    *lines: LineInput,
    on: date = MARCH_15,
) -> JournalEntry:
    return create_journal_entry_for_object(
        session,
        obj,
        entry_date=on,
        description="AWS bill INV-1001",
        lines=list(lines or AWS_BILL),
    )


def _journal_entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(JournalEntry)) or 0


def _link_count(session: Session) -> int:
    count = select(func.count()).select_from(AccountingObjectEntry)
    return session.scalar(count) or 0


def test_an_entry_created_for_an_object_is_a_linked_draft(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    books.commit()

    assert entry.status is JournalEntryStatus.DRAFT
    assert objects_for_journal_entry(books, entry) == [bill]
    impact = accounting_impact(books, bill)
    assert [e.entry_id for e in impact.entries] == [entry.id]
    assert impact.entries[0].linked
    assert impact.pending_entry_ids == (entry.id,)
    assert impact.activity == {}  # a draft posts nothing
    assert not impact.has_net_impact


def test_an_entry_the_kernel_cannot_record_is_neither_recorded_nor_linked(
    books: Session,
) -> None:
    bill = _bill(books)
    books.commit()

    with pytest.raises(JournalEntryError) as caught:
        _record(
            books,
            bill,
            LineInput("9999", debit=Decimal("1200.00")),
            LineInput("2110", credit=Decimal("-1200.00")),
        )
    books.flush()

    assert {issue.code for issue in caught.value.issues} == {
        IssueCode.UNKNOWN_ACCOUNT,
        IssueCode.NEGATIVE_AMOUNT,
    }
    assert _journal_entry_count(books) == 0
    assert _link_count(books) == 0


def test_posting_an_objects_entry_is_validated_by_the_kernel(books: Session) -> None:
    bill = _bill(books)
    unbalanced = _record(
        books,
        bill,
        LineInput("6100", debit=Decimal("1200.00")),
        LineInput("2110", credit=Decimal("1100.00")),
    )
    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, unbalanced)
    assert [issue.code for issue in caught.value.issues] == [IssueCode.UNBALANCED]

    close_period(get_period(books, "2026-02"))
    late = _record(books, bill, on=date(2026, 2, 28))
    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, late)
    assert [issue.code for issue in caught.value.issues] == [IssueCode.PERIOD_CLOSED]

    books.commit()
    assert ledger_lines(books) == []
    assert accounting_impact(books, bill).activity == {}


def test_a_posted_entry_is_the_objects_accounting_impact(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    post_journal_entry(books, entry)
    books.commit()

    impact = accounting_impact(books, bill)
    assert impact.activity == {
        "6100": Activity(debits=Decimal("1200.00")),
        "2110": Activity(credits=Decimal("1200.00")),
    }
    assert impact.has_net_impact
    assert impact.posted_entry_ids == (entry.id,)
    assert impact.pending_entry_ids == ()


def test_a_reversal_is_part_of_the_impact_without_being_linked(
    books: Session,
) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    post_journal_entry(books, entry)
    reversal = reverse_journal_entry(books, entry, entry_date=date(2026, 3, 31))
    books.commit()

    impact = accounting_impact(books, bill)
    assert [
        (e.entry_id, e.status, e.linked, e.reversal_of) for e in impact.entries
    ] == [
        (entry.id, JournalEntryStatus.REVERSED, True, None),
        (reversal.id, JournalEntryStatus.POSTED, False, entry.id),
    ]
    assert impact.activity == {
        "6100": Activity(Decimal("1200.00"), Decimal("1200.00")),
        "2110": Activity(Decimal("1200.00"), Decimal("1200.00")),
    }
    assert not impact.has_net_impact


def test_reversing_a_reversal_reinstates_the_impact(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    post_journal_entry(books, entry)
    reversal = reverse_journal_entry(books, entry, entry_date=date(2026, 3, 20))
    reinstated = reverse_journal_entry(books, reversal, entry_date=date(2026, 3, 25))
    books.commit()

    impact = accounting_impact(books, bill)
    assert [e.entry_id for e in impact.entries] == [
        entry.id,
        reversal.id,
        reinstated.id,
    ]
    assert impact.has_net_impact
    assert impact.posted_entry_ids == (reinstated.id,)


def test_drafts_and_voided_entries_contribute_nothing(books: Session) -> None:
    bill = _bill(books)
    posted = _record(books, bill)
    post_journal_entry(books, posted)
    _record(books, bill)
    abandoned = _record(books, bill)
    void_journal_entry(abandoned)
    books.commit()

    assert accounting_impact(books, bill).activity == {
        "6100": Activity(debits=Decimal("1200.00")),
        "2110": Activity(credits=Decimal("1200.00")),
    }


def test_one_entry_can_record_several_objects(books: Session) -> None:
    first, second = _bill(books, "INV-1"), _bill(books, "INV-2")
    payment = create_accounting_object(
        books,
        object_type="vendor_payment",
        occurred_at=RECEIVED,
        source="bank_feed",
        entity_id="V-AWS",
    )
    entry = create_journal_entry_for_object(
        books,
        payment,
        entry_date=MARCH_15,
        description="Pay INV-1 and INV-2",
        lines=[
            LineInput("2110", debit=Decimal("2400.00")),
            LineInput("1111", credit=Decimal("2400.00")),
        ],
    )
    link_journal_entry(books, first, entry)
    link_journal_entry(books, second, entry)
    post_journal_entry(books, entry)
    books.commit()

    assert objects_for_journal_entry(books, entry) == [first, second, payment]
    for obj in (first, second, payment):
        assert accounting_impact(books, obj).posted_entry_ids == (entry.id,)


def test_a_posted_entry_can_be_linked_after_the_fact_without_changing_it(
    books: Session,
) -> None:
    entry = create_journal_entry(
        books, entry_date=MARCH_15, description="AWS", lines=list(AWS_BILL)
    )
    post_journal_entry(books, entry)
    books.commit()
    posted_at, updated_at = entry.posted_at, entry.updated_at

    bill = _bill(books)
    link_journal_entry(books, bill, entry)
    books.commit()

    assert entry.status is JournalEntryStatus.POSTED
    assert (entry.posted_at, entry.updated_at) == (posted_at, updated_at)
    assert accounting_impact(books, bill).has_net_impact


def test_an_entry_records_an_object_only_once(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    with pytest.raises(AlreadyLinkedError):
        link_journal_entry(books, bill, entry)


def test_a_voided_entry_cannot_be_linked(books: Session) -> None:
    entry = create_journal_entry(
        books, entry_date=MARCH_15, description="AWS", lines=list(AWS_BILL)
    )
    void_journal_entry(entry)
    with pytest.raises(EntryStatusError, match="records nothing"):
        link_journal_entry(books, _bill(books), entry)


def test_an_object_with_entries_that_could_still_post_cannot_be_voided(
    books: Session,
) -> None:
    bill = _bill(books)
    draft = _record(books, bill)

    with pytest.raises(ObjectHasAccountingImpactError, match="void them") as caught:
        void_accounting_object(books, bill)
    assert caught.value.entry_ids == (draft.id,)
    assert bill.status is AccountingObjectStatus.OBSERVED


def test_an_object_the_ledger_still_carries_cannot_be_voided(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    post_journal_entry(books, entry)

    with pytest.raises(ObjectHasAccountingImpactError, match="reverse them") as caught:
        void_accounting_object(books, bill)
    assert caught.value.entry_ids == (entry.id,)
    assert bill.status is AccountingObjectStatus.OBSERVED


def test_an_object_is_voided_once_its_impact_nets_to_nothing(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    post_journal_entry(books, entry)
    reverse_journal_entry(books, entry, entry_date=date(2026, 3, 31))
    void_accounting_object(books, bill)
    books.commit()

    assert bill.status is AccountingObjectStatus.VOIDED
    assert len(ledger_lines(books)) == 4  # the entry and its reversal both remain
    assert trial_balance(books, as_of=date(2026, 3, 31)).lines == ()


def test_an_object_without_entries_can_be_voided(books: Session) -> None:
    duplicate = _bill(books)
    void_accounting_object(books, duplicate)
    assert duplicate.is_voided


def test_a_voided_object_cannot_gain_entries(books: Session) -> None:
    duplicate = _bill(books)
    void_accounting_object(books, duplicate)
    books.commit()

    with pytest.raises(VoidedObjectError):
        _record(books, duplicate)
    assert _journal_entry_count(books) == 0

    entry = create_journal_entry(
        books, entry_date=MARCH_15, description="AWS", lines=list(AWS_BILL)
    )
    with pytest.raises(VoidedObjectError):
        link_journal_entry(books, duplicate, entry)

    # Not even by constructing the link directly.
    books.add(AccountingObjectEntry(accounting_object=duplicate, journal_entry=entry))
    with pytest.raises(VoidedObjectError):
        books.flush()


def test_a_voided_object_is_final(books: Session) -> None:
    duplicate = _bill(books)
    void_accounting_object(books, duplicate)
    books.commit()

    with pytest.raises(VoidedObjectError, match="already VOIDED"):
        void_accounting_object(books, duplicate)

    duplicate.status = AccountingObjectStatus.OBSERVED
    with pytest.raises(VoidedObjectError, match="cannot change"):
        books.flush()
    books.rollback()

    duplicate.data = {"invoice_number": "INV-9999"}
    with pytest.raises(VoidedObjectError, match="cannot change"):
        books.flush()


def test_a_voided_object_still_receives_business_events(books: Session) -> None:
    duplicate = _bill(books)
    void_accounting_object(books, duplicate)
    books.commit()

    record_accounting_event(
        books,
        duplicate,
        event_type="vendor_confirmed_duplicate",
        occurred_at=RECEIVED,
        source="email",
    )
    books.commit()
    assert [event.event_type for event in duplicate.events] == [
        "vendor_confirmed_duplicate"
    ]


def test_links_are_permanent(books: Session) -> None:
    bill = _bill(books)
    entry = _record(books, bill)
    books.commit()

    books.delete(bill.entry_links[0])
    with pytest.raises(ImmutableRecordError, match="reverse or void"):
        books.flush()
    books.rollback()

    # A linked draft is abandoned by voiding it; the database refuses to lose it.
    books.delete(entry)
    with pytest.raises(IntegrityError):
        books.flush()


def test_objects_never_reach_the_ledger_by_themselves(books: Session) -> None:
    for number in range(3):
        bill = _bill(books, f"INV-{number}")
        record_accounting_event(
            books, bill, event_type="received", occurred_at=RECEIVED, source="email"
        )
    books.commit()

    assert ledger_lines(books) == []
    assert trial_balance(books, as_of=date(2026, 12, 31)).lines == ()

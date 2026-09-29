"""The accounting object workflow: observe, extract, classify, void."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from opensumma.kernel import LineInput, post_journal_entry
from opensumma.objects import (
    AccountingObject,
    AccountingObjectStatus,
    CounterpartyKindError,
    InactiveCounterpartyError,
    ObjectHasAccountingImpactError,
    create_journal_entry_for_object,
    deactivate_counterparty,
    get_counterparty,
)
from opensumma.workflow import (
    Actor,
    CounterpartyRequiredError,
    InvalidTransitionError,
    PermissionDeniedError,
    classify_accounting_object,
    extract_accounting_object,
    observe_accounting_object,
    void_accounting_object,
    workflow_history,
)

Stage = AccountingObjectStatus
RECEIVED = datetime(2026, 3, 15, 9, 30, tzinfo=UTC)


def _observe(
    session: Session, actor: Actor, object_type: str = "vendor_bill"
) -> AccountingObject:
    return observe_accounting_object(
        session,
        actor=actor,
        object_type=object_type,
        occurred_at=RECEIVED,
        source="email",
        data={"raw": "Stratus Cloud Hosting, invoice INV-88, total 310.00"},
    )


def _stages(session: Session, obj: AccountingObject) -> list[tuple[str, ...]]:
    return [
        (t.action.value, t.from_status or "-", t.to_status, t.actor.code)
        for t in workflow_history(session, obj)
    ]


def test_an_agent_takes_a_bill_from_observed_to_classified(
    books: Session, agent: Actor
) -> None:
    bill = _observe(books, agent)
    extract_accounting_object(
        books,
        bill,
        actor=agent,
        data={"invoice_number": "INV-88", "amount": "310.00"},
    )
    classify_accounting_object(
        books, bill, actor=agent, counterparty="V-STRATUS", reason="Name on invoice"
    )
    books.commit()

    assert bill.status is Stage.CLASSIFIED
    assert bill.data == {"invoice_number": "INV-88", "amount": "310.00"}
    assert bill.counterparty is get_counterparty(books, "V-STRATUS")
    assert _stages(books, bill) == [
        ("observe", "-", "OBSERVED", "je-agent"),
        ("extract", "OBSERVED", "EXTRACTED", "je-agent"),
        ("classify", "EXTRACTED", "CLASSIFIED", "je-agent"),
    ]


def test_extracted_data_is_business_data(books: Session, agent: Actor) -> None:
    bill = _observe(books, agent)
    with pytest.raises(TypeError, match="write amounts as strings"):
        extract_accounting_object(books, bill, actor=agent, data={"amount": 310.0})
    assert bill.status is Stage.OBSERVED


def test_extracting_again_sends_a_classified_object_back(
    books: Session, agent: Actor
) -> None:
    bill = _observe(books, agent)
    classify_accounting_object(books, bill, actor=agent, counterparty="V-STRATUS")
    extract_accounting_object(books, bill, actor=agent, data={"amount": "320.00"})
    assert bill.status is Stage.EXTRACTED


def test_a_bill_is_classified_only_with_a_vendor(books: Session, agent: Actor) -> None:
    bill = _observe(books, agent)
    with pytest.raises(CounterpartyRequiredError, match="vendor"):
        classify_accounting_object(books, bill, actor=agent)
    with pytest.raises(CounterpartyKindError, match="customer"):
        classify_accounting_object(books, bill, actor=agent, counterparty="C-HELIO")

    deactivate_counterparty(get_counterparty(books, "V-SUMMIT"))
    with pytest.raises(InactiveCounterpartyError):
        classify_accounting_object(books, bill, actor=agent, counterparty="V-SUMMIT")
    assert bill.status is Stage.OBSERVED
    assert bill.counterparty is None


def test_an_object_that_names_nobody_is_classified_as_it_is(
    books: Session, agent: Actor
) -> None:
    fee = _observe(books, agent, "bank_transaction")
    classify_accounting_object(books, fee, actor=agent)
    assert fee.status is Stage.CLASSIFIED
    assert fee.counterparty is None


def test_observing_needs_the_proposer_permission(
    books: Session, controller: Actor
) -> None:
    with pytest.raises(PermissionDeniedError, match="PROPOSER"):
        _observe(books, controller)


def test_voiding_an_object_is_an_approvers_decision(
    books: Session, agent: Actor, controller: Actor
) -> None:
    duplicate = _observe(books, agent)
    with pytest.raises(PermissionDeniedError, match="APPROVER"):
        void_accounting_object(books, duplicate, actor=agent, reason="Duplicate")
    with pytest.raises(ValueError, match="reason is required"):
        void_accounting_object(books, duplicate, actor=controller, reason="")

    void_accounting_object(books, duplicate, actor=controller, reason="Duplicate")
    assert duplicate.status is Stage.VOIDED
    assert _stages(books, duplicate)[-1] == ("void", "OBSERVED", "VOIDED", "controller")

    steps: list[Callable[[], None]] = [
        lambda: extract_accounting_object(books, duplicate, actor=agent, data={}),
        lambda: classify_accounting_object(books, duplicate, actor=agent),
    ]
    for step in steps:
        with pytest.raises(InvalidTransitionError):
            step()


def test_an_object_the_ledger_carries_is_not_voided(
    books: Session, agent: Actor, controller: Actor
) -> None:
    bill = _observe(books, agent)
    classify_accounting_object(books, bill, actor=agent, counterparty="V-STRATUS")
    entry = create_journal_entry_for_object(
        books,
        bill,
        entry_date=RECEIVED.date(),
        description="Stratus INV-88",
        lines=[
            LineInput("5200", debit=Decimal("310.00")),
            LineInput("2110", credit=Decimal("310.00")),
        ],
    )
    post_journal_entry(books, entry)
    books.commit()

    with pytest.raises(ObjectHasAccountingImpactError):
        void_accounting_object(books, bill, actor=controller, reason="Duplicate")
    books.flush()
    assert bill.status is Stage.CLASSIFIED
    assert [t.action.value for t in workflow_history(books, bill)] == [
        "observe",
        "classify",
    ]


def test_a_float_cannot_reach_data_through_the_workflow_either(
    books: Session, agent: Actor
) -> None:
    bill = _observe(books, agent)
    bill.data = {"amount": 1.5}  # bypassing extract still meets the column's check
    with pytest.raises(StatementError, match="write amounts as strings"):
        books.flush()

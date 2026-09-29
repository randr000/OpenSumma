"""The mutating tools: each is the workflow operation of the same name.

The workflow checks the state, the permission, and its controls before the kernel
checks the content, and records every attempt, allowed or refused, in the audit
log with the reason and evidence given. A tool that names a record that does not
exist is refused before it reaches the workflow, and so is not audited, as on the
REST interface.
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field

from opensumma import kernel, workflow
from opensumma.interface import schemas, views
from opensumma.interface.work import unit_of_work
from opensumma.kernel import LineInput
from opensumma.mcp.context import ToolContext, acting
from opensumma.objects import get_accounting_object

Reason = Annotated[
    str | None,
    Field(
        description="Why, concisely: at most 500 characters, kept in the audit log. "
        "Never private reasoning."
    ),
]
RequiredReason = Annotated[
    str,
    Field(
        description="Why, concisely: required, at most 500 characters, kept in "
        "the audit log."
    ),
]
Evidence = Annotated[
    tuple[str, ...],
    Field(
        description="References to what supports the action, such as "
        '"vendor_id=42" or "invoice=INV-2002": at most 50, each at most 200 '
        "characters."
    ),
]


def propose_journal_entry(
    ctx: ToolContext,
    entry_date: date,
    description: str,
    lines: list[schemas.LineIn],
    accounting_object_id: int | None = None,
    reason: Reason = None,
    evidence: Evidence = (),
) -> schemas.JournalEntryOut:
    """Propose a journal entry (PROPOSER), linked to an accounting object if given.

    Each line names an account code and either a debit or a credit, as a decimal
    string such as "120.50", with optional dimensions such as {"DEPARTMENT":
    "ENG"}. An entry that cannot be recorded at all is refused with issue codes;
    one that can be recorded but not posted, such as an unbalanced one, is
    proposed, and validate_journal_entry says why.
    """
    with acting(ctx) as (session, actor):
        obj = (
            None
            if accounting_object_id is None
            else get_accounting_object(session, accounting_object_id)
        )
        with unit_of_work(session):
            entry = workflow.propose_journal_entry(
                session,
                actor=actor,
                entry_date=entry_date,
                description=description,
                lines=[
                    LineInput(
                        line.account,
                        debit=Decimal(line.debit),
                        credit=Decimal(line.credit),
                        memo=line.memo,
                        dimensions=line.dimensions,
                    )
                    for line in lines
                ],
                accounting_object=obj,
                reason=reason,
                evidence=evidence,
            )
        return views.journal_entry(session, entry)


def validate_journal_entry(
    ctx: ToolContext, entry_id: int, reason: Reason = None, evidence: Evidence = ()
) -> schemas.ValidationOut:
    """Every reason the entry could not be posted now, each with a stable issue code
    (PROPOSER). Changes nothing but the audit log. A proposed entry cannot be
    edited: void it and propose a corrected one."""
    with acting(ctx) as (session, actor):
        entry = kernel.get_journal_entry(session, entry_id)
        with unit_of_work(session):
            found = workflow.validate_journal_entry(
                session, entry, actor=actor, reason=reason, evidence=evidence
            )
        return schemas.ValidationOut(
            journal_entry_id=entry_id, is_valid=not found, issues=views.issues(found)
        )


Step = Callable[..., None]


def _step(
    ctx: ToolContext,
    operation: Step,
    entry_id: int,
    reason: str | None,
    evidence: Sequence[str],
) -> schemas.JournalEntryOut:
    with acting(ctx) as (session, actor):
        entry = kernel.get_journal_entry(session, entry_id)
        with unit_of_work(session):
            operation(session, entry, actor=actor, reason=reason, evidence=evidence)
        return views.journal_entry(session, entry)


def submit_for_approval(
    ctx: ToolContext, entry_id: int, reason: Reason = None, evidence: Evidence = ()
) -> schemas.JournalEntryOut:
    """Submit a valid PROPOSED entry for approval (PROPOSER); its content locks."""
    return _step(ctx, workflow.submit_for_approval, entry_id, reason, evidence)


def approve_journal_entry(
    ctx: ToolContext, entry_id: int, reason: Reason = None, evidence: Evidence = ()
) -> schemas.JournalEntryOut:
    """Approve an entry PENDING_APPROVAL (APPROVER), never by whoever proposed or
    submitted it."""
    return _step(ctx, workflow.approve_journal_entry, entry_id, reason, evidence)


def reject_journal_entry(
    ctx: ToolContext, entry_id: int, reason: RequiredReason, evidence: Evidence = ()
) -> schemas.JournalEntryOut:
    """Return an entry PENDING_APPROVAL to its preparer as PROPOSED (APPROVER),
    saying why."""
    return _step(ctx, workflow.reject_journal_entry, entry_id, reason, evidence)


def post_journal_entry(
    ctx: ToolContext, entry_id: int, reason: Reason = None, evidence: Evidence = ()
) -> schemas.JournalEntryOut:
    """Post an APPROVED entry to the ledger (POSTER). A posted entry is never
    edited or deleted; it can only be reversed."""
    return _step(ctx, workflow.post_journal_entry, entry_id, reason, evidence)


def void_journal_entry(
    ctx: ToolContext, entry_id: int, reason: RequiredReason, evidence: Evidence = ()
) -> schemas.JournalEntryOut:
    """Abandon an entry that was never posted, saying why: PROPOSER for a proposal,
    APPROVER for one under review or approved."""
    return _step(ctx, workflow.void_journal_entry, entry_id, reason, evidence)


def reverse_journal_entry(
    ctx: ToolContext,
    entry_id: int,
    entry_date: date,
    description: str | None = None,
    reason: Reason = None,
    evidence: Evidence = (),
) -> schemas.JournalEntryOut:
    """Post a reversal of a POSTED entry, dated ``entry_date`` (POSTER), and return
    the reversal. The original stays in the ledger, marked REVERSED."""
    with acting(ctx) as (session, actor):
        entry = kernel.get_journal_entry(session, entry_id)
        with unit_of_work(session):
            reversal = workflow.reverse_journal_entry(
                session,
                entry,
                actor=actor,
                entry_date=entry_date,
                description=description,
                reason=reason,
                evidence=evidence,
            )
        return views.journal_entry(session, reversal)


def observe_accounting_object(
    ctx: ToolContext,
    object_type: str,
    occurred_at: datetime,
    source: str,
    counterparty: str | None = None,
    data: dict[str, Any] | None = None,
    reason: Reason = None,
    evidence: Evidence = (),
) -> schemas.AccountingObjectOut:
    """Record a business document or event as it arrives (PROPOSER), such as a
    vendor_bill from email. ``occurred_at`` needs a time zone. Business data is
    JSON; write amounts in it as strings, because a non-integer number is
    refused."""
    with acting(ctx) as (session, actor):
        with unit_of_work(session):
            obj = workflow.observe_accounting_object(
                session,
                actor=actor,
                object_type=object_type,
                occurred_at=occurred_at,
                source=source,
                counterparty=counterparty,
                data=data,
                reason=reason,
                evidence=evidence,
            )
        return views.accounting_object(session, obj)


def extract_accounting_object(
    ctx: ToolContext,
    object_id: int,
    data: dict[str, Any],
    reason: Reason = None,
    evidence: Evidence = (),
) -> schemas.AccountingObjectOut:
    """Replace an OBSERVED object's business data with what was read from its
    source (PROPOSER)."""
    with acting(ctx) as (session, actor):
        obj = get_accounting_object(session, object_id)
        with unit_of_work(session):
            workflow.extract_accounting_object(
                session, obj, actor=actor, data=data, reason=reason, evidence=evidence
            )
        return views.accounting_object(session, obj)


def classify_accounting_object(
    ctx: ToolContext,
    object_id: int,
    counterparty: str | None = None,
    reason: Reason = None,
    evidence: Evidence = (),
) -> schemas.AccountingObjectOut:
    """Settle the vendor or customer an object concerns, by counterparty code
    (PROPOSER). An entry for an object whose type names a vendor or customer, such
    as a vendor_bill, can be proposed only once its counterparty is settled."""
    with acting(ctx) as (session, actor):
        obj = get_accounting_object(session, object_id)
        with unit_of_work(session):
            workflow.classify_accounting_object(
                session,
                obj,
                actor=actor,
                counterparty=counterparty,
                reason=reason,
                evidence=evidence,
            )
        return views.accounting_object(session, obj)


def void_accounting_object(
    ctx: ToolContext, object_id: int, reason: RequiredReason, evidence: Evidence = ()
) -> schemas.AccountingObjectOut:
    """Withdraw an object the ledger does not carry (APPROVER), saying why. One
    the ledger still carries must have its entries reversed first."""
    with acting(ctx) as (session, actor):
        obj = get_accounting_object(session, object_id)
        with unit_of_work(session):
            workflow.void_accounting_object(
                session, obj, actor=actor, reason=reason, evidence=evidence
            )
        return views.accounting_object(session, obj)


def close_period(
    ctx: ToolContext, code: str, reason: Reason = None, evidence: Evidence = ()
) -> schemas.PeriodOut:
    """Close an accounting period, such as "2026-03" (ADMIN), after every earlier
    one, once no entry dated in it is waiting to be posted."""
    with acting(ctx) as (session, actor):
        period = kernel.get_period(session, code)
        with unit_of_work(session):
            workflow.close_period(
                session, period, actor=actor, reason=reason, evidence=evidence
            )
        return views.period(period)


def reopen_period(
    ctx: ToolContext, code: str, reason: RequiredReason, evidence: Evidence = ()
) -> schemas.PeriodOut:
    """Reopen the latest closed period (ADMIN), saying why."""
    with acting(ctx) as (session, actor):
        period = kernel.get_period(session, code)
        with unit_of_work(session):
            workflow.reopen_period(
                session, period, actor=actor, reason=reason, evidence=evidence
            )
        return views.period(period)


TOOLS: list[Callable[..., Any]] = [
    propose_journal_entry,
    validate_journal_entry,
    submit_for_approval,
    approve_journal_entry,
    reject_journal_entry,
    post_journal_entry,
    void_journal_entry,
    reverse_journal_entry,
    observe_accounting_object,
    extract_accounting_object,
    classify_accounting_object,
    void_accounting_object,
    close_period,
    reopen_period,
]

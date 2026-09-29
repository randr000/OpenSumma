"""Journal entries through the workflow, and the ledger they post to."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session

from opensumma import kernel, workflow
from opensumma.api import schemas, views
from opensumma.api.dependencies import ActorDep, ReaderDep, SessionDep, unit_of_work
from opensumma.kernel import JournalEntry, LineInput
from opensumma.objects import get_accounting_object

router = APIRouter(tags=["journal entries"])


@router.post("/journal-entries", status_code=201)
def propose_journal_entry(
    body: schemas.Proposal, session: SessionDep, actor: ActorDep
) -> schemas.JournalEntryOut:
    """Propose a journal entry (PROPOSER), linked to an accounting object if given.

    Amounts are strings. An entry that cannot be recorded at all is refused with
    its issue codes; one that can be recorded but not posted yet, such as an
    unbalanced one, is proposed, and ``validate`` says why.
    """
    obj = (
        None
        if body.accounting_object_id is None
        else get_accounting_object(session, body.accounting_object_id)
    )
    with unit_of_work(session):
        entry = workflow.propose_journal_entry(
            session,
            actor=actor,
            entry_date=body.entry_date,
            description=body.description,
            lines=[
                LineInput(
                    line.account,
                    debit=Decimal(line.debit),
                    credit=Decimal(line.credit),
                    memo=line.memo,
                    dimensions=line.dimensions,
                )
                for line in body.lines
            ],
            accounting_object=obj,
            reason=body.reason,
            evidence=body.evidence,
        )
    return views.journal_entry(session, entry)


@router.get("/journal-entries/{entry_id}")
def get_journal_entry(
    entry_id: int, session: SessionDep, reader: ReaderDep
) -> schemas.JournalEntryOut:
    """One journal entry, with its lines and the accounting objects it records."""
    return views.journal_entry(session, kernel.get_journal_entry(session, entry_id))


@router.post("/journal-entries/{entry_id}/validate")
def validate_journal_entry(
    entry_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.ValidationOut:
    """Every reason the entry could not be posted now (PROPOSER). Changes nothing
    but the audit log."""
    body = body or schemas.Action()
    entry = kernel.get_journal_entry(session, entry_id)
    with unit_of_work(session):
        found = workflow.validate_journal_entry(
            session, entry, actor=actor, reason=body.reason, evidence=body.evidence
        )
    return schemas.ValidationOut(
        journal_entry_id=entry_id, is_valid=not found, issues=views.issues(found)
    )


Step = Callable[..., None]


def _step(
    operation: Step,
    entry_id: int,
    session: Session,
    actor: workflow.Actor,
    body: schemas.Action | None,
    *,
    reason_required: bool = False,
) -> schemas.JournalEntryOut:
    body = body or schemas.Action()
    entry: JournalEntry = kernel.get_journal_entry(session, entry_id)
    reason = (body.reason or "") if reason_required else body.reason
    with unit_of_work(session):
        operation(session, entry, actor=actor, reason=reason, evidence=body.evidence)
    return views.journal_entry(session, entry)


@router.post("/journal-entries/{entry_id}/submit")
def submit_for_approval(
    entry_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.JournalEntryOut:
    """Submit a valid entry for approval (PROPOSER); its content locks."""
    return _step(workflow.submit_for_approval, entry_id, session, actor, body)


@router.post("/journal-entries/{entry_id}/approve")
def approve_journal_entry(
    entry_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.JournalEntryOut:
    """Approve a pending entry (APPROVER); never by whoever prepared it."""
    return _step(workflow.approve_journal_entry, entry_id, session, actor, body)


@router.post("/journal-entries/{entry_id}/reject")
def reject_journal_entry(
    entry_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.JournalEntryOut:
    """Return a pending entry to its preparer (APPROVER), with a reason."""
    return _step(
        workflow.reject_journal_entry,
        entry_id,
        session,
        actor,
        body,
        reason_required=True,
    )


@router.post("/journal-entries/{entry_id}/post")
def post_journal_entry(
    entry_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.JournalEntryOut:
    """Post an approved entry to the ledger (POSTER)."""
    return _step(workflow.post_journal_entry, entry_id, session, actor, body)


@router.post("/journal-entries/{entry_id}/void")
def void_journal_entry(
    entry_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.JournalEntryOut:
    """Abandon an entry that was never posted, with a reason."""
    return _step(
        workflow.void_journal_entry,
        entry_id,
        session,
        actor,
        body,
        reason_required=True,
    )


@router.post("/journal-entries/{entry_id}/reverse", status_code=201)
def reverse_journal_entry(
    entry_id: int, body: schemas.Reversal, session: SessionDep, actor: ActorDep
) -> schemas.JournalEntryOut:
    """Post a reversal of a posted entry (POSTER), and return the reversal."""
    entry = kernel.get_journal_entry(session, entry_id)
    with unit_of_work(session):
        reversal = workflow.reverse_journal_entry(
            session,
            entry,
            actor=actor,
            entry_date=body.entry_date,
            description=body.description,
            reason=body.reason,
            evidence=body.evidence,
        )
    return views.journal_entry(session, reversal)


@router.get("/ledger", tags=["ledger"])
def get_ledger(
    session: SessionDep,
    reader: ReaderDep,
    start: date | None = None,
    end: date | None = None,
    account: Annotated[list[str] | None, Query()] = None,
) -> list[schemas.LedgerLineOut]:
    """Posted ledger lines by date, entry, and line, optionally for some accounts
    (repeat ``account``) between two dates, both inclusive."""
    lines = kernel.ledger_lines(session, start=start, end=end, account_codes=account)
    return [views.ledger_line(line) for line in lines]

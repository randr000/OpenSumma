"""The journal entry workflow: propose, submit, approve or reject, post, reverse.

These operations carry the names of the agent tools that will invoke them, and wrap
the kernel operations of the same name. Each one checks, in order:

1. that the action is allowed from the entry's current state (``machine``);
2. that the actor holds the permission it needs;
3. any reason the action requires;
4. the workflow's controls: an entry is never approved by an actor who prepared it,
   and an entry for an object that names a vendor or customer is proposed only once
   that counterparty is settled;
5. the entry's content, through the kernel's validation.

If any check fails, nothing changes. The kernel's own operations remain available to
trusted code, such as the dataset generator; every interface an actor can reach goes
through these instead.
"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.kernel import (
    JournalEntry,
    JournalEntryError,
    JournalEntryStatus,
    LineInput,
    ValidationIssue,
    create_journal_entry,
)
from opensumma.objects import AccountingObject, create_journal_entry_for_object
from opensumma.workflow.actors import require_permission
from opensumma.workflow.enums import Permission, WorkflowAction
from opensumma.workflow.errors import (
    CounterpartyRequiredError,
    SegregationOfDutiesError,
)
from opensumma.workflow.machine import (
    JOURNAL_ENTRY_WORKFLOW,
    Transition,
    authorize,
    reason_text,
    record_transition,
)
from opensumma.workflow.models import Actor, WorkflowTransition

_PREPARING = (WorkflowAction.PROPOSE, WorkflowAction.SUBMIT)


def propose_journal_entry(
    session: Session,
    *,
    actor: Actor,
    entry_date: date,
    description: str,
    lines: Sequence[LineInput],
    accounting_object: AccountingObject | None = None,
    reason: str | None = None,
) -> JournalEntry:
    """Record a PROPOSED journal entry, linked to ``accounting_object`` if given.

    The kernel records it, so a ``JournalEntryError`` lists everything that stops it
    from being stored at all. An entry that can be stored but not yet posted, such
    as an unbalanced one, is recorded; ``validate_journal_entry`` says why.
    """
    transition = authorize(JOURNAL_ENTRY_WORKFLOW, WorkflowAction.PROPOSE, None, actor)
    note = reason_text(reason)
    if accounting_object is None:
        entry = create_journal_entry(
            session, entry_date=entry_date, description=description, lines=lines
        )
    else:
        _require_counterparty(accounting_object)
        entry = create_journal_entry_for_object(
            session,
            accounting_object,
            entry_date=entry_date,
            description=description,
            lines=lines,
        )
    entry.status = JournalEntryStatus(transition.target)
    record_transition(
        session, entry, transition, actor=actor, from_status=None, reason=note
    )
    return entry


def validate_journal_entry(
    session: Session, entry: JournalEntry, *, actor: Actor
) -> list[ValidationIssue]:
    """Every reason ``entry`` could not be posted now; empty when it could.

    Changes nothing. Proposers use it to check and correct their work.
    """
    require_permission(actor, Permission.PROPOSER)
    return kernel.validate_journal_entry(session, entry)


def submit_for_approval(
    session: Session, entry: JournalEntry, *, actor: Actor, reason: str | None = None
) -> None:
    """Send ``entry`` to an approver. Its content is locked from here on.

    Only an entry that passes validation can be submitted; a ``JournalEntryError``
    lists every issue otherwise.
    """
    transition = authorize(
        JOURNAL_ENTRY_WORKFLOW, WorkflowAction.SUBMIT, entry.status, actor
    )
    note = reason_text(reason)
    _require_valid(session, entry)
    _move(session, entry, transition, actor, note)


def approve_journal_entry(
    session: Session, entry: JournalEntry, *, actor: Actor, reason: str | None = None
) -> None:
    """Approve a pending entry, so that a poster may post it.

    The approver must not have proposed or submitted it. The entry is validated
    again, because master data may have changed since it was submitted.
    """
    transition = authorize(
        JOURNAL_ENTRY_WORKFLOW, WorkflowAction.APPROVE, entry.status, actor
    )
    note = reason_text(reason)
    if actor.id in _preparers(session, entry):
        raise SegregationOfDutiesError(
            f"actor {actor.code} prepared journal entry {entry.id}, so another "
            "actor must approve it"
        )
    _require_valid(session, entry)
    _move(session, entry, transition, actor, note)


def reject_journal_entry(
    session: Session, entry: JournalEntry, *, actor: Actor, reason: str
) -> None:
    """Return a pending entry to its preparer as PROPOSED, saying why."""
    transition = authorize(
        JOURNAL_ENTRY_WORKFLOW, WorkflowAction.REJECT, entry.status, actor
    )
    note = reason_text(reason, required_for="reject a journal entry")
    _move(session, entry, transition, actor, note)


def post_journal_entry(
    session: Session, entry: JournalEntry, *, actor: Actor, reason: str | None = None
) -> None:
    """Post an approved entry to the ledger, through the kernel's validation."""
    transition = authorize(
        JOURNAL_ENTRY_WORKFLOW, WorkflowAction.POST, entry.status, actor
    )
    note = reason_text(reason)
    before = entry.status
    kernel.post_journal_entry(session, entry)
    record_transition(
        session, entry, transition, actor=actor, from_status=before, reason=note
    )


def reverse_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    entry_date: date,
    description: str | None = None,
    reason: str | None = None,
) -> JournalEntry:
    """Post a reversal of ``entry`` through the kernel, and return it.

    Both the reversed entry and the new reversing entry record the step.
    """
    transition = authorize(
        JOURNAL_ENTRY_WORKFLOW, WorkflowAction.REVERSE, entry.status, actor
    )
    note = reason_text(reason)
    before = entry.status
    reversal = kernel.reverse_journal_entry(
        session, entry, entry_date=entry_date, description=description
    )
    record_transition(
        session, entry, transition, actor=actor, from_status=before, reason=note
    )
    created = authorize(JOURNAL_ENTRY_WORKFLOW, WorkflowAction.REVERSE, None, actor)
    record_transition(
        session, reversal, created, actor=actor, from_status=None, reason=note
    )
    return reversal


def void_journal_entry(
    session: Session, entry: JournalEntry, *, actor: Actor, reason: str
) -> None:
    """Abandon an entry that was never posted, saying why."""
    transition = authorize(
        JOURNAL_ENTRY_WORKFLOW, WorkflowAction.VOID, entry.status, actor
    )
    note = reason_text(reason, required_for="void a journal entry")
    before = entry.status
    kernel.void_journal_entry(entry)
    record_transition(
        session, entry, transition, actor=actor, from_status=before, reason=note
    )


def _move(
    session: Session,
    entry: JournalEntry,
    transition: Transition,
    actor: Actor,
    reason: str | None,
) -> None:
    before = entry.status
    entry.status = JournalEntryStatus(transition.target)
    record_transition(
        session, entry, transition, actor=actor, from_status=before, reason=reason
    )


def _require_valid(session: Session, entry: JournalEntry) -> None:
    if issues := kernel.validate_journal_entry(session, entry):
        raise JournalEntryError(issues)


def _require_counterparty(obj: AccountingObject) -> None:
    kind = obj.object_type.counterparty_kind
    if kind is not None and obj.counterparty is None:
        raise CounterpartyRequiredError(
            f"accounting object {obj.id} is a {obj.object_type.value}, which names a "
            f"{kind.value.lower()}; settle its counterparty before proposing an entry"
        )


def _preparers(session: Session, entry: JournalEntry) -> set[int]:
    """The actors who proposed or submitted ``entry``."""
    session.flush()
    statement = select(WorkflowTransition.actor_id).where(
        WorkflowTransition.journal_entry_id == entry.id,
        WorkflowTransition.action.in_(_PREPARING),
    )
    return set(session.scalars(statement))

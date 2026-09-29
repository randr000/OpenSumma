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

Every operation is audited: its attempt, succeeded or refused, becomes one audit
event with the actor, input, output, reason, and evidence.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
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
from opensumma.workflow.audit import AuditScope, audited, issues_json
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
    evidence: Sequence[str] = (),
) -> JournalEntry:
    """Record a PROPOSED journal entry, linked to ``accounting_object`` if given.

    The kernel records it, so a ``JournalEntryError`` lists everything that stops it
    from being stored at all. An entry that can be stored but not yet posted, such
    as an unbalanced one, is recorded; ``validate_journal_entry`` says why.
    """
    lines = list(lines)
    with audited(
        session,
        actor=actor,
        action="propose_journal_entry",
        input={
            "entry_date": entry_date,
            "description": description,
            "lines": lines,
            "accounting_object_id": _id(accounting_object),
        },
        reason=reason,
        evidence=evidence,
    ) as audit:
        transition = authorize(
            JOURNAL_ENTRY_WORKFLOW, WorkflowAction.PROPOSE, None, actor
        )
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
        audit.subject = entry
        audit.output = {"status": entry.status}
    return entry


def validate_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> list[ValidationIssue]:
    """Every reason ``entry`` could not be posted now; empty when it could.

    Changes nothing but the audit log. Proposers use it to check and correct their
    work.
    """
    with _audited(
        session, "validate_journal_entry", entry, actor, reason, evidence
    ) as audit:
        require_permission(actor, Permission.PROPOSER)
        issues = kernel.validate_journal_entry(session, entry)
        audit.output = {"issues": issues_json(issues)}
    return issues


def submit_for_approval(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> None:
    """Send ``entry`` to an approver. Its content is locked from here on.

    Only an entry that passes validation can be submitted; a ``JournalEntryError``
    lists every issue otherwise.
    """
    with _audited(
        session, "submit_for_approval", entry, actor, reason, evidence
    ) as audit:
        transition = authorize(
            JOURNAL_ENTRY_WORKFLOW, WorkflowAction.SUBMIT, entry.status, actor
        )
        note = reason_text(reason)
        _require_valid(session, entry)
        _move(session, entry, transition, actor, note)
        audit.output = {"status": entry.status}


def approve_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> None:
    """Approve a pending entry, so that a poster may post it.

    The approver must not have proposed or submitted it. The entry is validated
    again, because master data may have changed since it was submitted.
    """
    with _audited(
        session, "approve_journal_entry", entry, actor, reason, evidence
    ) as audit:
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
        audit.output = {"status": entry.status}


def reject_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    reason: str,
    evidence: Sequence[str] = (),
) -> None:
    """Return a pending entry to its preparer as PROPOSED, saying why."""
    with _audited(
        session, "reject_journal_entry", entry, actor, reason, evidence
    ) as audit:
        transition = authorize(
            JOURNAL_ENTRY_WORKFLOW, WorkflowAction.REJECT, entry.status, actor
        )
        note = reason_text(reason, required_for="reject a journal entry")
        _move(session, entry, transition, actor, note)
        audit.output = {"status": entry.status}


def post_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> None:
    """Post an approved entry to the ledger, through the kernel's validation."""
    with _audited(
        session, "post_journal_entry", entry, actor, reason, evidence
    ) as audit:
        transition = authorize(
            JOURNAL_ENTRY_WORKFLOW, WorkflowAction.POST, entry.status, actor
        )
        note = reason_text(reason)
        before = entry.status
        kernel.post_journal_entry(session, entry)
        record_transition(
            session, entry, transition, actor=actor, from_status=before, reason=note
        )
        audit.output = {"status": entry.status}


def reverse_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    entry_date: date,
    description: str | None = None,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> JournalEntry:
    """Post a reversal of ``entry`` through the kernel, and return it.

    Both the reversed entry and the new reversing entry record the step.
    """
    with _audited(
        session,
        "reverse_journal_entry",
        entry,
        actor,
        reason,
        evidence,
        entry_date=entry_date,
        description=description,
    ) as audit:
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
        session.flush()  # the reversal's id belongs in the audit event
        audit.output = {"status": entry.status, "reversal_id": reversal.id}
    return reversal


def void_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    actor: Actor,
    reason: str,
    evidence: Sequence[str] = (),
) -> None:
    """Abandon an entry that was never posted, saying why."""
    with _audited(
        session, "void_journal_entry", entry, actor, reason, evidence
    ) as audit:
        transition = authorize(
            JOURNAL_ENTRY_WORKFLOW, WorkflowAction.VOID, entry.status, actor
        )
        note = reason_text(reason, required_for="void a journal entry")
        before = entry.status
        kernel.void_journal_entry(entry)
        record_transition(
            session, entry, transition, actor=actor, from_status=before, reason=note
        )
        audit.output = {"status": entry.status}


def _audited(
    session: Session,
    action: str,
    entry: JournalEntry,
    actor: Actor,
    reason: str | None,
    evidence: Sequence[str],
    **details: object,
) -> AbstractContextManager[AuditScope]:
    return audited(
        session,
        actor=actor,
        action=action,
        subject=entry,
        input={"journal_entry_id": entry.id, **details},
        reason=reason,
        evidence=evidence,
    )


def _id(record: AccountingObject | None) -> int | None:
    return None if record is None else record.id


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

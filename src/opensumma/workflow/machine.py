"""The state machines: which actions move a subject from which state to which, and
the permission each needs.

The tables below are the whole of the rules about *when* an action is allowed.
Checks on content (is the entry balanced? does the bill name a vendor?) belong to
the operations that take the action, and to the kernel and object layer beneath.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma.kernel import AccountingPeriod, JournalEntry, JournalEntryStatus
from opensumma.kernel import PeriodStatus as Period
from opensumma.objects import AccountingObject
from opensumma.objects import AccountingObjectStatus as Object
from opensumma.workflow.actors import require_permission
from opensumma.workflow.enums import Permission, WorkflowAction
from opensumma.workflow.errors import InvalidTransitionError
from opensumma.workflow.models import Actor, WorkflowTransition

Entry = JournalEntryStatus
Action = WorkflowAction
Subject = JournalEntry | AccountingObject | AccountingPeriod

REASON_LENGTH = 500


@dataclass(frozen=True)
class Transition:
    """``action`` moves a subject from any of ``sources`` to ``target``.

    A source of ``None`` means the action brings the subject into being.
    """

    action: WorkflowAction
    sources: tuple[StrEnum | None, ...]
    target: StrEnum
    permission: Permission


JOURNAL_ENTRY_WORKFLOW: tuple[Transition, ...] = (
    Transition(Action.PROPOSE, (None,), Entry.PROPOSED, Permission.PROPOSER),
    Transition(
        Action.SUBMIT,
        (Entry.DRAFT, Entry.PROPOSED),
        Entry.PENDING_APPROVAL,
        Permission.PROPOSER,
    ),
    Transition(
        Action.APPROVE, (Entry.PENDING_APPROVAL,), Entry.APPROVED, Permission.APPROVER
    ),
    Transition(
        Action.REJECT, (Entry.PENDING_APPROVAL,), Entry.PROPOSED, Permission.APPROVER
    ),
    Transition(Action.POST, (Entry.APPROVED,), Entry.POSTED, Permission.POSTER),
    Transition(Action.REVERSE, (Entry.POSTED,), Entry.REVERSED, Permission.POSTER),
    # The reversing entry itself comes into being posted.
    Transition(Action.REVERSE, (None,), Entry.POSTED, Permission.POSTER),
    # A preparer may withdraw their own work; once it is under review or approved,
    # withdrawing it is an approver's decision.
    Transition(
        Action.VOID, (Entry.DRAFT, Entry.PROPOSED), Entry.VOIDED, Permission.PROPOSER
    ),
    Transition(
        Action.VOID,
        (Entry.PENDING_APPROVAL, Entry.APPROVED),
        Entry.VOIDED,
        Permission.APPROVER,
    ),
)

_UNVOIDED = (Object.OBSERVED, Object.EXTRACTED, Object.CLASSIFIED)

ACCOUNTING_OBJECT_WORKFLOW: tuple[Transition, ...] = (
    Transition(Action.OBSERVE, (None,), Object.OBSERVED, Permission.PROPOSER),
    # Extracting again after classification sends the object back to be classified.
    Transition(Action.EXTRACT, _UNVOIDED, Object.EXTRACTED, Permission.PROPOSER),
    Transition(Action.CLASSIFY, _UNVOIDED, Object.CLASSIFIED, Permission.PROPOSER),
    Transition(Action.VOID, _UNVOIDED, Object.VOIDED, Permission.APPROVER),
)

ACCOUNTING_PERIOD_WORKFLOW: tuple[Transition, ...] = (
    Transition(Action.CLOSE, (Period.OPEN,), Period.CLOSED, Permission.ADMIN),
    Transition(Action.REOPEN, (Period.CLOSED,), Period.OPEN, Permission.ADMIN),
)


def authorize(
    workflow: tuple[Transition, ...],
    action: WorkflowAction,
    current: StrEnum | None,
    actor: Actor,
) -> Transition:
    """The transition ``action`` makes from ``current``, if ``actor`` may make it.

    Raises ``InvalidTransitionError`` if the action is not allowed from the current
    state, then ``PermissionDeniedError`` if the actor may not take it.
    """
    for transition in workflow:
        if transition.action is action and current in transition.sources:
            require_permission(actor, transition.permission)
            return transition
    allowed = sorted(
        str(source)
        for transition in workflow
        if transition.action is action
        for source in transition.sources
        if source is not None
    )
    state = "a new subject" if current is None else str(current)
    raise InvalidTransitionError(
        f"cannot {action.value} from {state}; allowed from {', '.join(allowed)}",
        action,
        current,
    )


def record_transition(
    session: Session,
    subject: Subject,
    transition: Transition,
    *,
    actor: Actor,
    from_status: StrEnum | None,
    reason: str | None = None,
) -> WorkflowTransition:
    """Append ``transition`` to ``subject``'s workflow history."""
    record = WorkflowTransition(
        action=transition.action,
        from_status=None if from_status is None else str(from_status),
        to_status=str(transition.target),
        actor=actor,
        reason=reason,
    )
    if isinstance(subject, JournalEntry):
        record.journal_entry = subject
    elif isinstance(subject, AccountingObject):
        record.accounting_object = subject
    else:
        record.accounting_period = subject
    session.add(record)
    return record


def workflow_history(session: Session, subject: Subject) -> list[WorkflowTransition]:
    """Every workflow transition of ``subject``, oldest first."""
    session.flush()
    if isinstance(subject, JournalEntry):
        column = WorkflowTransition.journal_entry_id
    elif isinstance(subject, AccountingObject):
        column = WorkflowTransition.accounting_object_id
    else:
        column = WorkflowTransition.accounting_period_id
    statement = (
        select(WorkflowTransition)
        .where(column == subject.id)
        .order_by(WorkflowTransition.id)
    )
    return list(session.scalars(statement))


def reason_text(reason: str | None, *, required_for: str | None = None) -> str | None:
    """``reason`` stripped, or ``None``; raise if it is required and missing."""
    text = (reason or "").strip()
    if not text:
        if required_for is not None:
            raise ValueError(f"a reason is required to {required_for}")
        return None
    if len(text) > REASON_LENGTH:
        raise ValueError(f"a reason is limited to {REASON_LENGTH} characters")
    return text

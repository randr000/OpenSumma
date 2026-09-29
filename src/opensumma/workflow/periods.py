"""Closing and reopening accounting periods: ADMIN actions, taken in order.

Periods close in chronological order and reopen in reverse order, so the closed
periods are always the earliest ones. Every posting then falls after the last
closed period, and a closed period's reports never change.

A period also cannot close while an entry dated in it could still be posted: a
draft, a proposal, or an entry awaiting approval or posting would otherwise be
stranded in a period that no longer accepts postings.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.kernel import (
    AccountingPeriod,
    JournalEntry,
    JournalEntryStatus,
    PeriodStatus,
)
from opensumma.workflow.enums import WorkflowAction
from opensumma.workflow.errors import PendingEntriesError, PeriodSequenceError
from opensumma.workflow.machine import (
    ACCOUNTING_PERIOD_WORKFLOW,
    authorize,
    reason_text,
    record_transition,
)
from opensumma.workflow.models import Actor

_UNFINISHED = tuple(status for status in JournalEntryStatus if not status.is_final)


def close_period(
    session: Session,
    period: AccountingPeriod,
    *,
    actor: Actor,
    reason: str | None = None,
) -> None:
    """Close ``period`` to further postings.

    Every earlier period must already be closed, and no entry dated in this one
    may still be awaiting posting.
    """
    transition = authorize(
        ACCOUNTING_PERIOD_WORKFLOW, WorkflowAction.CLOSE, period.status, actor
    )
    note = reason_text(reason)
    earlier_open = session.scalars(
        select(AccountingPeriod.code)
        .where(
            AccountingPeriod.start_date < period.start_date,
            AccountingPeriod.status == PeriodStatus.OPEN,
        )
        .order_by(AccountingPeriod.start_date)
    ).all()
    if earlier_open:
        raise PeriodSequenceError(
            f"periods close in order; close {', '.join(earlier_open)} before "
            f"{period.code}",
            earlier_open,
        )
    unfinished = session.scalars(
        select(JournalEntry.id)
        .where(
            JournalEntry.entry_date.between(period.start_date, period.end_date),
            JournalEntry.status.in_(_UNFINISHED),
        )
        .order_by(JournalEntry.id)
    ).all()
    if unfinished:
        raise PendingEntriesError(
            f"period {period.code} has journal entries that could still be posted "
            f"({', '.join(str(i) for i in unfinished)}); post or void them first",
            unfinished,
        )
    before = period.status
    kernel.close_period(period)
    record_transition(
        session, period, transition, actor=actor, from_status=before, reason=note
    )


def reopen_period(
    session: Session, period: AccountingPeriod, *, actor: Actor, reason: str
) -> None:
    """Reopen ``period`` for corrections, saying why.

    Every later period must be open, so only the latest closed period reopens.
    """
    transition = authorize(
        ACCOUNTING_PERIOD_WORKFLOW, WorkflowAction.REOPEN, period.status, actor
    )
    note = reason_text(reason, required_for="reopen a period")
    later_closed = session.scalars(
        select(AccountingPeriod.code)
        .where(
            AccountingPeriod.start_date > period.start_date,
            AccountingPeriod.status == PeriodStatus.CLOSED,
        )
        .order_by(AccountingPeriod.start_date.desc())
    ).all()
    if later_closed:
        raise PeriodSequenceError(
            f"periods reopen in reverse order; reopen {', '.join(later_closed)} "
            f"before {period.code}",
            later_closed,
        )
    before = period.status
    kernel.reopen_period(period)
    record_transition(
        session, period, transition, actor=actor, from_status=before, reason=note
    )

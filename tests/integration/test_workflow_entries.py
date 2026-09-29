"""The journal entry workflow: propose, validate, submit, approve or reject, post,
reverse, and void, each by an actor holding the permission it needs."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.kernel import (
    ImmutableEntryError,
    IssueCode,
    JournalEntry,
    JournalEntryError,
    JournalEntryStatus,
    JournalLine,
    LineInput,
    close_period,
    deactivate_account,
    get_account,
    get_period,
    ledger_lines,
)
from opensumma.objects import create_accounting_object
from opensumma.workflow import (
    Actor,
    CounterpartyRequiredError,
    ImmutableHistoryError,
    InvalidTransitionError,
    PermissionDeniedError,
    SegregationOfDutiesError,
    WorkflowTransition,
    approve_journal_entry,
    post_journal_entry,
    propose_journal_entry,
    reject_journal_entry,
    reverse_journal_entry,
    set_actor_permissions,
    submit_for_approval,
    validate_journal_entry,
    void_journal_entry,
    workflow_history,
)

Status = JournalEntryStatus
MARCH_15 = date(2026, 3, 15)
RENT = (
    LineInput("6200", debit=Decimal("2500.00")),
    LineInput("1111", credit=Decimal("2500.00")),
)


def _propose(session: Session, actor: Actor, *lines: LineInput) -> JournalEntry:
    return propose_journal_entry(
        session,
        actor=actor,
        entry_date=MARCH_15,
        description="March rent",
        lines=list(lines or RENT),
    )


def _history(session: Session, entry: JournalEntry) -> list[tuple[str, ...]]:
    return [
        (t.action.value, t.from_status or "-", t.to_status, t.actor.code)
        for t in workflow_history(session, entry)
    ]


def _status(entry: JournalEntry) -> JournalEntryStatus:
    """The entry's status, read afresh (it changes as the workflow acts on it)."""
    return entry.status


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_an_entry_goes_from_proposal_to_the_ledger(
    books: Session, agent: Actor, controller: Actor, poster: Actor
) -> None:
    entry = _propose(books, agent)
    submit_for_approval(books, entry, actor=agent)
    approve_journal_entry(books, entry, actor=controller, reason="Matches the lease")
    post_journal_entry(books, entry, actor=poster)
    books.commit()

    assert entry.status is Status.POSTED
    assert {line.entry_id for line in ledger_lines(books)} == {entry.id}
    assert _history(books, entry) == [
        ("propose", "-", "PROPOSED", "je-agent"),
        ("submit", "PROPOSED", "PENDING_APPROVAL", "je-agent"),
        ("approve", "PENDING_APPROVAL", "APPROVED", "controller"),
        ("post", "APPROVED", "POSTED", "poster"),
    ]
    approval = workflow_history(books, entry)[2]
    assert approval.reason == "Matches the lease"
    assert approval.created_at.tzinfo is not None


def test_a_proposal_the_kernel_cannot_record_leaves_no_trace(
    books: Session, agent: Actor
) -> None:
    with pytest.raises(JournalEntryError) as caught:
        _propose(
            books,
            agent,
            LineInput("9999", debit=Decimal("10.00")),
            LineInput("1111", credit=Decimal("10.00")),
        )
    books.flush()
    assert [issue.code for issue in caught.value.issues] == [IssueCode.UNKNOWN_ACCOUNT]
    assert _count(books, JournalEntry) == 0
    assert _count(books, WorkflowTransition) == 0


def test_an_unbalanced_proposal_is_recorded_but_cannot_be_submitted(
    books: Session, agent: Actor
) -> None:
    entry = _propose(
        books,
        agent,
        LineInput("6200", debit=Decimal("2500.00")),
        LineInput("1111", credit=Decimal("2400.00")),
    )
    assert [i.code for i in validate_journal_entry(books, entry, actor=agent)] == [
        IssueCode.UNBALANCED
    ]
    with pytest.raises(JournalEntryError):
        submit_for_approval(books, entry, actor=agent)
    assert entry.status is Status.PROPOSED
    assert [t.action.value for t in workflow_history(books, entry)] == ["propose"]


def test_validating_needs_the_proposer_permission(
    books: Session, agent: Actor, poster: Actor
) -> None:
    entry = _propose(books, agent)
    with pytest.raises(PermissionDeniedError):
        validate_journal_entry(books, entry, actor=poster)


def test_a_normal_agent_can_neither_approve_nor_post(
    books: Session, agent: Actor, clerk: Actor
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    with pytest.raises(PermissionDeniedError, match="APPROVER"):
        approve_journal_entry(books, entry, actor=agent)

    entry.status = Status.APPROVED  # as if approved, to test posting alone
    with pytest.raises(PermissionDeniedError, match="POSTER"):
        post_journal_entry(books, entry, actor=agent)


def test_an_entry_is_never_approved_by_whoever_prepared_it(
    books: Session, clerk: Actor, controller: Actor
) -> None:
    # A clerk who has also been made an approver still cannot approve their own work.
    set_actor_permissions(clerk, ["PROPOSER", "APPROVER"])
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    with pytest.raises(SegregationOfDutiesError):
        approve_journal_entry(books, entry, actor=clerk)
    assert _status(entry) is Status.PENDING_APPROVAL

    approve_journal_entry(books, entry, actor=controller)
    assert _status(entry) is Status.APPROVED


def test_whoever_submits_a_draft_counts_as_its_preparer(
    books: Session, clerk: Actor, controller: Actor
) -> None:
    draft = kernel.create_journal_entry(
        books, entry_date=MARCH_15, description="Rent", lines=list(RENT)
    )
    set_actor_permissions(controller, ["PROPOSER", "APPROVER"])
    submit_for_approval(books, draft, actor=controller)
    with pytest.raises(SegregationOfDutiesError):
        approve_journal_entry(books, draft, actor=controller)


def test_approval_revalidates_against_current_master_data(
    books: Session, clerk: Actor, controller: Actor
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    deactivate_account(get_account(books, "6200"))

    with pytest.raises(JournalEntryError) as caught:
        approve_journal_entry(books, entry, actor=controller)
    assert [i.code for i in caught.value.issues] == [IssueCode.ACCOUNT_INACTIVE]
    assert entry.status is Status.PENDING_APPROVAL


def test_a_rejected_entry_returns_to_its_preparer_for_correction(
    books: Session, clerk: Actor, controller: Actor, poster: Actor
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    with pytest.raises(ValueError, match="reason is required"):
        reject_journal_entry(books, entry, actor=controller, reason="  ")

    reject_journal_entry(books, entry, actor=controller, reason="Rent is 2,450")
    books.commit()
    assert entry.status is Status.PROPOSED

    # Its content can be corrected again, then it goes round once more.
    entry.lines[0].debit = Decimal("2450.00")
    entry.lines[1].credit = Decimal("2450.00")
    books.commit()
    submit_for_approval(books, entry, actor=clerk)
    approve_journal_entry(books, entry, actor=controller)
    post_journal_entry(books, entry, actor=poster)
    books.commit()

    assert [t[0] for t in _history(books, entry)] == [
        "propose",
        "submit",
        "reject",
        "submit",
        "approve",
        "post",
    ]
    assert workflow_history(books, entry)[2].reason == "Rent is 2,450"


@pytest.mark.parametrize("status", [Status.PENDING_APPROVAL, Status.APPROVED])
def test_what_is_under_review_or_approved_cannot_change(
    books: Session, clerk: Actor, controller: Actor, status: JournalEntryStatus
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    if status is Status.APPROVED:
        approve_journal_entry(books, entry, actor=controller)
    books.commit()

    entry.description = "Rent, adjusted after approval"
    with pytest.raises(ImmutableEntryError, match="locked"):
        books.flush()
    books.rollback()

    entry.lines[0].debit = Decimal("9999.00")
    with pytest.raises(ImmutableEntryError):
        books.flush()
    books.rollback()

    entry.lines.append(
        JournalLine(
            line_number=3, account=get_account(books, "6100"), debit=Decimal("1.00")
        )
    )
    with pytest.raises(ImmutableEntryError):
        books.flush()
    books.rollback()

    books.delete(entry)
    with pytest.raises(ImmutableEntryError, match="void it instead"):
        books.flush()


def test_posting_goes_through_the_kernels_validation(
    books: Session, clerk: Actor, controller: Actor, poster: Actor
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    approve_journal_entry(books, entry, actor=controller)
    close_period(get_period(books, "2026-03"))  # closed after approval

    with pytest.raises(JournalEntryError) as caught:
        post_journal_entry(books, entry, actor=poster)
    assert [i.code for i in caught.value.issues] == [IssueCode.PERIOD_CLOSED]
    assert entry.status is Status.APPROVED
    assert [t.action.value for t in workflow_history(books, entry)][-1] == "approve"


def test_a_posted_entry_is_reversed_by_a_poster(
    books: Session, clerk: Actor, controller: Actor, poster: Actor
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    with pytest.raises(InvalidTransitionError, match="cannot reverse from"):
        reverse_journal_entry(books, entry, actor=poster, entry_date=MARCH_15)
    approve_journal_entry(books, entry, actor=controller)
    post_journal_entry(books, entry, actor=poster)

    with pytest.raises(PermissionDeniedError):
        reverse_journal_entry(books, entry, actor=controller, entry_date=MARCH_15)
    reversal = reverse_journal_entry(
        books, entry, actor=poster, entry_date=date(2026, 3, 31), reason="Duplicate"
    )
    books.commit()

    assert (entry.status, reversal.status) == (Status.REVERSED, Status.POSTED)
    assert _history(books, entry)[-1] == ("reverse", "POSTED", "REVERSED", "poster")
    assert _history(books, reversal) == [("reverse", "-", "POSTED", "poster")]


def test_who_may_void_depends_on_how_far_the_entry_has_gone(
    books: Session, clerk: Actor, controller: Actor
) -> None:
    withdrawn = _propose(books, clerk)
    with pytest.raises(ValueError, match="reason is required"):
        void_journal_entry(books, withdrawn, actor=clerk, reason="")
    void_journal_entry(books, withdrawn, actor=clerk, reason="Proposed twice")
    assert withdrawn.status is Status.VOIDED

    pending = _propose(books, clerk)
    submit_for_approval(books, pending, actor=clerk)
    with pytest.raises(PermissionDeniedError, match="APPROVER"):
        void_journal_entry(books, pending, actor=clerk, reason="Changed my mind")
    void_journal_entry(books, pending, actor=controller, reason="Not our lease")
    assert pending.status is Status.VOIDED

    with pytest.raises(InvalidTransitionError):
        submit_for_approval(books, pending, actor=clerk)


def test_nothing_changes_when_a_step_is_refused(
    books: Session, clerk: Actor, agent: Actor
) -> None:
    entry = _propose(books, clerk)
    books.commit()
    before = _count(books, WorkflowTransition)

    attempts: list[Callable[[], None]] = [
        lambda: approve_journal_entry(books, entry, actor=agent),
        lambda: post_journal_entry(books, entry, actor=agent),
        lambda: reject_journal_entry(books, entry, actor=agent, reason="x"),
    ]
    for attempt in attempts:
        with pytest.raises((InvalidTransitionError, PermissionDeniedError)):
            attempt()
    books.flush()

    assert entry.status is Status.PROPOSED
    assert _count(books, WorkflowTransition) == before


def test_an_entry_for_a_vendor_bill_waits_for_its_vendor(
    books: Session, agent: Actor
) -> None:
    bill = create_accounting_object(
        books,
        object_type="vendor_bill",
        occurred_at=datetime(2026, 3, 15, 9, 0, tzinfo=UTC),
        source="email",
    )
    with pytest.raises(CounterpartyRequiredError, match="vendor"):
        propose_journal_entry(
            books,
            actor=agent,
            entry_date=MARCH_15,
            description="Unknown vendor",
            lines=list(RENT),
            accounting_object=bill,
        )
    books.flush()
    assert _count(books, JournalEntry) == 0


def test_the_workflow_history_is_append_only(books: Session, clerk: Actor) -> None:
    entry = _propose(books, clerk)
    books.commit()
    (proposal,) = workflow_history(books, entry)

    proposal.reason = "rewritten"
    with pytest.raises(ImmutableHistoryError):
        books.flush()
    books.rollback()

    books.delete(proposal)
    with pytest.raises(ImmutableHistoryError):
        books.flush()
    books.rollback()

    for statement in (
        update(WorkflowTransition).values(reason="rewritten"),
        delete(WorkflowTransition),
    ):
        with pytest.raises(ImmutableHistoryError, match="bulk"):
            books.execute(statement)

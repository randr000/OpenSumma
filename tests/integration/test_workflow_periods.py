"""Closing and reopening periods: ADMIN actions, taken in order."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.kernel import (
    LineInput,
    PeriodStatus,
    create_journal_entry,
    get_period,
    periods,
)
from opensumma.workflow import (
    Actor,
    InvalidTransitionError,
    PendingEntriesError,
    PeriodSequenceError,
    PermissionDeniedError,
    close_period,
    propose_journal_entry,
    reopen_period,
    void_journal_entry,
    workflow_history,
)


def _close(session: Session, actor: Actor, *codes: str) -> None:
    for code in codes:
        close_period(session, get_period(session, code), actor=actor)


def _closed(session: Session) -> list[str]:
    return [p.code for p in periods(session) if p.status is PeriodStatus.CLOSED]


def test_only_an_admin_closes_or_reopens_a_period(
    books: Session, admin: Actor, controller: Actor
) -> None:
    january = get_period(books, "2026-01")
    with pytest.raises(PermissionDeniedError, match="ADMIN"):
        close_period(books, january, actor=controller)

    close_period(books, january, actor=admin, reason="January is done")
    with pytest.raises(PermissionDeniedError, match="ADMIN"):
        reopen_period(books, january, actor=controller, reason="Late invoice")
    with pytest.raises(InvalidTransitionError):
        close_period(books, january, actor=admin)

    reopen_period(books, january, actor=admin, reason="Late invoice")
    assert [
        (t.action.value, t.from_status, t.to_status, t.reason)
        for t in workflow_history(books, january)
    ] == [
        ("close", "OPEN", "CLOSED", "January is done"),
        ("reopen", "CLOSED", "OPEN", "Late invoice"),
    ]


def test_periods_close_in_order(books: Session, admin: Actor) -> None:
    with pytest.raises(PeriodSequenceError) as caught:
        _close(books, admin, "2026-03")
    assert caught.value.period_codes == ("2026-01", "2026-02")
    assert _closed(books) == []

    _close(books, admin, "2026-01", "2026-02", "2026-03")
    assert _closed(books) == ["2026-01", "2026-02", "2026-03"]


def test_periods_reopen_in_reverse_order(books: Session, admin: Actor) -> None:
    _close(books, admin, "2026-01", "2026-02", "2026-03")

    with pytest.raises(PeriodSequenceError) as caught:
        reopen_period(books, get_period(books, "2026-01"), actor=admin, reason="Fix")
    assert caught.value.period_codes == ("2026-03", "2026-02")
    with pytest.raises(ValueError, match="reason is required"):
        reopen_period(books, get_period(books, "2026-03"), actor=admin, reason="")

    reopen_period(books, get_period(books, "2026-03"), actor=admin, reason="Fix")
    assert _closed(books) == ["2026-01", "2026-02"]


def test_a_period_does_not_close_over_entries_still_awaiting_posting(
    books: Session, admin: Actor, clerk: Actor
) -> None:
    lines = [
        LineInput("6200", Decimal("100.00")),
        LineInput("1111", Decimal("-100.00")),
    ]
    draft = create_journal_entry(
        books, entry_date=date(2026, 1, 20), description="Draft", lines=lines
    )
    proposal = propose_journal_entry(
        books,
        actor=clerk,
        entry_date=date(2026, 1, 31),
        description="Proposal",
        lines=lines,
    )
    create_journal_entry(  # dated in February, so it does not hold January open
        books, entry_date=date(2026, 2, 1), description="Next month", lines=lines
    )
    books.flush()

    with pytest.raises(PendingEntriesError) as caught:
        _close(books, admin, "2026-01")
    assert caught.value.entry_ids == (draft.id, proposal.id)

    void_journal_entry(books, proposal, actor=clerk, reason="Not needed")
    kernel.void_journal_entry(draft)  # abandoned outside the workflow
    _close(books, admin, "2026-01")
    assert _closed(books) == ["2026-01"]

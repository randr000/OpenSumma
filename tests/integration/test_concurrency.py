"""Transactions at the same time, which only PostgreSQL runs.

SQLite lets one transaction write at a time, so no test on it ever sees two at
once; PostgreSQL runs them together, and these tests make them race on purpose.
Each race is choreographed: the first transaction acts, and while it is still
open a second, in another thread, acts on the same thing; then the first commits.
Unless the second waits for the first and then sees what it did, it acts on what
the books were before, and both succeed where only one may.
"""

import threading
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import (
    JournalEntry,
    JournalEntryError,
    JournalEntryStatus,
    LineInput,
    create_calendar_year_periods,
    get_period,
    seed_chart_of_accounts,
)
from opensumma.workflow import (
    ActorType,
    InvalidTransitionError,
    Permission,
    approve_journal_entry,
    audit_history,
    close_period,
    create_actor,
    get_actor,
    post_journal_entry,
    propose_journal_entry,
    reverse_journal_entry,
    submit_for_approval,
    verify_audit_log,
    workflow_history,
)

pytestmark = pytest.mark.postgresql

CAST = {
    "clerk": (ActorType.HUMAN, [Permission.PROPOSER]),
    "maria": (ActorType.HUMAN, [Permission.APPROVER]),
    "omar": (ActorType.HUMAN, [Permission.APPROVER]),
    "poster-1": (ActorType.SYSTEM, [Permission.POSTER]),
    "poster-2": (ActorType.SYSTEM, [Permission.POSTER]),
    "admin": (ActorType.HUMAN, [Permission.ADMIN]),
}
WRITERS = 4
ACTIONS = 10

Action = Callable[[Session], object]


@pytest.fixture
def books(database_url: str) -> Iterator[Engine]:
    """Migrated books with a chart, the periods of 2026, and the cast above."""
    init_db(database_url)
    engine = create_engine(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        create_calendar_year_periods(session, 2026)
        for code, (kind, permissions) in CAST.items():
            create_actor(
                session, code=code, name=code, actor_type=kind, permissions=permissions
            )
        session.commit()
    yield engine
    engine.dispose()


def _entry(session: Session, day: date = date(2026, 1, 10)) -> JournalEntry:
    return propose_journal_entry(
        session,
        actor=get_actor(session, "clerk"),
        entry_date=day,
        description="Office supplies",
        lines=[
            LineInput("6700", Decimal("45.00")),
            LineInput("2110", Decimal("-45.00")),
        ],
    )


def _prepare(engine: Engine, *, until: JournalEntryStatus) -> int:
    """An entry taken through the workflow until it reaches ``until``."""
    with Session(engine) as session:
        entry = _entry(session)
        steps = [
            (submit_for_approval, "clerk"),
            (approve_journal_entry, "maria"),
            (post_journal_entry, "poster-1"),
        ]
        for step, actor in steps:
            if entry.status is until:
                break
            step(session, entry, actor=get_actor(session, actor))
        assert entry.status is until
        session.commit()
        return entry.id


def _at_once(engine: Engine, first: Action, second: Action) -> Exception | None:
    """Run ``first``, and while its transaction is open, ``second`` in another
    thread; then commit ``first``. Return what stopped ``second``, if anything."""
    stopped: list[Exception] = []

    def run_second() -> None:
        try:
            with Session(engine) as session:
                second(session)
                session.commit()
        except Exception as error:
            stopped.append(error)

    with Session(engine) as session:
        first(session)
        session.flush()
        racer = threading.Thread(target=run_second)
        racer.start()
        racer.join(timeout=1.0)  # long enough for it to reach the first's rows
        session.commit()
    racer.join(timeout=30)
    assert not racer.is_alive(), "the second transaction never finished"
    return stopped[0] if stopped else None


def _act(step: Callable[..., object], entry_id: int, actor: str) -> Action:
    def act(session: Session) -> object:
        entry = session.get(JournalEntry, entry_id)
        assert entry is not None
        return step(session, entry, actor=get_actor(session, actor))

    return act


def test_the_audit_log_stays_one_chain_under_concurrent_writers(
    books: Engine,
) -> None:
    start = threading.Barrier(WRITERS)
    failures: list[Exception] = []

    def write() -> None:
        try:
            with Session(books) as session:
                start.wait()
                for _ in range(ACTIONS):
                    _entry(session)
                    session.commit()
        except Exception as error:
            failures.append(error)

    writers = [threading.Thread(target=write) for _ in range(WRITERS)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=60)
    assert failures == []

    with Session(books) as session:
        events = audit_history(session)
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        proposals = audit_history(session, action="propose_journal_entry")
        assert len(proposals) == WRITERS * ACTIONS
        assert verify_audit_log(session).is_intact


def test_of_two_approvals_at_once_only_the_first_succeeds(books: Engine) -> None:
    entry_id = _prepare(books, until=JournalEntryStatus.PENDING_APPROVAL)
    stopped = _at_once(
        books,
        _act(approve_journal_entry, entry_id, "maria"),
        _act(approve_journal_entry, entry_id, "omar"),
    )
    assert isinstance(stopped, InvalidTransitionError)
    with Session(books) as session:
        entry = session.get(JournalEntry, entry_id)
        assert entry is not None
        approvals = [
            step.actor.code
            for step in workflow_history(session, entry)
            if step.action.value == "approve"
        ]
        assert approvals == ["maria"]


def test_of_two_postings_at_once_only_the_first_succeeds(books: Engine) -> None:
    entry_id = _prepare(books, until=JournalEntryStatus.APPROVED)
    stopped = _at_once(
        books,
        _act(post_journal_entry, entry_id, "poster-1"),
        _act(post_journal_entry, entry_id, "poster-2"),
    )
    assert isinstance(stopped, InvalidTransitionError)
    with Session(books) as session:
        entry = session.get(JournalEntry, entry_id)
        assert entry is not None
        posted = [
            s for s in workflow_history(session, entry) if s.action.value == "post"
        ]
        assert [step.actor.code for step in posted] == ["poster-1"]


def _close_january(session: Session) -> None:
    close_period(
        session, get_period(session, "2026-01"), actor=get_actor(session, "admin")
    )


def _reverse_into_january(entry_id: int) -> Action:
    def reverse(session: Session) -> object:
        entry = session.get(JournalEntry, entry_id)
        assert entry is not None
        return reverse_journal_entry(
            session,
            entry,
            actor=get_actor(session, "poster-2"),
            entry_date=date(2026, 1, 31),
        )

    return reverse


def test_a_period_closed_while_an_entry_is_posted_into_it_receives_nothing(
    books: Engine,
) -> None:
    entry_id = _prepare(books, until=JournalEntryStatus.POSTED)
    stopped = _at_once(books, _close_january, _reverse_into_january(entry_id))
    assert isinstance(stopped, JournalEntryError)
    assert [issue.code.value for issue in stopped.issues] == ["PERIOD_CLOSED"]
    with Session(books) as session:
        reversals = session.scalars(
            select(JournalEntry).where(JournalEntry.reversal_of_id == entry_id)
        ).all()
        assert reversals == []


def test_a_period_closes_after_an_entry_being_posted_into_it(books: Engine) -> None:
    entry_id = _prepare(books, until=JournalEntryStatus.POSTED)
    # The other way round, the close waits for the posting, and then may go ahead:
    # the entry reached the period while it was open.
    assert _at_once(books, _reverse_into_january(entry_id), _close_january) is None
    with Session(books) as session:
        assert get_period(session, "2026-01").status.value == "CLOSED"
        reversal = session.scalars(
            select(JournalEntry).where(JournalEntry.reversal_of_id == entry_id)
        ).one()
        assert reversal.status is JournalEntryStatus.POSTED

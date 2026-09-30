"""Property-based test: whatever actors attempt, the workflow's guarantees hold.

Hypothesis proposes a journal entry, balanced or not, then throws a random sequence
of workflow actions at it, each by a random actor, some allowed and most not; a few
telling sequences are always tried as well. After every sequence:

- the entry's history is one unbroken chain, from its proposal to its current status;
- exactly the actions that succeeded were recorded, and refused ones changed nothing;
- every approval was by an actor who had not prepared the entry, and holds APPROVER;
- nothing reached the ledger without an approval before its posting, by a POSTER;
- the ledger balances.

Each example runs in a transaction that is rolled back.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from itertools import pairwise

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from opensumma.kernel import (
    JournalEntry,
    JournalEntryError,
    LineInput,
    create_calendar_year_periods,
    posted_activity,
    seed_chart_of_accounts,
    seed_dimensions,
)
from opensumma.workflow import (
    Actor,
    ActorType,
    Permission,
    WorkflowAction,
    WorkflowError,
    approve_journal_entry,
    create_actor,
    post_journal_entry,
    propose_journal_entry,
    reject_journal_entry,
    reverse_journal_entry,
    submit_for_approval,
    void_journal_entry,
    workflow_history,
)

P = Permission
CAST = {
    "agent": (ActorType.AGENT, (P.READ_ONLY, P.PROPOSER)),
    "clerk": (ActorType.HUMAN, (P.PROPOSER, P.APPROVER)),
    "controller": (ActorType.HUMAN, (P.APPROVER,)),
    "poster": (ActorType.SYSTEM, (P.POSTER,)),
    "everything": (ActorType.HUMAN, tuple(P)),
}
PREPARERS = ["agent", "clerk", "everything"]
STEPS = ["submit", "approve", "reject", "post", "reverse", "void"]


@pytest.fixture(scope="module")
def database(module_engine: Engine) -> Engine:
    return module_engine


@contextmanager
def _books(engine: Engine) -> Iterator[tuple[Session, dict[str, Actor]]]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            seed_chart_of_accounts(session)
            seed_dimensions(session)
            create_calendar_year_periods(session, 2026)
            cast = {
                code: create_actor(
                    session,
                    code=code,
                    name=code,
                    actor_type=kind,
                    permissions=permissions,
                )
                for code, (kind, permissions) in CAST.items()
            }
            session.flush()
            yield session, cast
        transaction.rollback()


def _attempt(session: Session, entry: JournalEntry, step: str, actor: Actor) -> None:
    if step == "submit":
        submit_for_approval(session, entry, actor=actor)
    elif step == "approve":
        approve_journal_entry(session, entry, actor=actor)
    elif step == "reject":
        reject_journal_entry(session, entry, actor=actor, reason="Please correct")
    elif step == "post":
        post_journal_entry(session, entry, actor=actor)
    elif step == "reverse":
        reverse_journal_entry(session, entry, actor=actor, entry_date=date(2026, 3, 31))
    else:
        void_journal_entry(session, entry, actor=actor, reason="Withdrawn")


# Sequences worth trying every time, on top of the random ones: random search alone
# rarely lines up a submission and then a self-approval by one of the actors able to
# do both.
@example(
    preparer="clerk", balanced=True, steps=[("submit", "clerk"), ("approve", "clerk")]
)
@example(
    preparer="agent",
    balanced=True,
    steps=[
        ("submit", "agent"),
        ("approve", "controller"),
        ("post", "poster"),
        ("reverse", "poster"),
    ],
)
@example(
    preparer="everything",
    balanced=True,
    steps=[
        ("submit", "everything"),
        ("approve", "everything"),
        ("approve", "clerk"),
        ("post", "everything"),
    ],
)
@example(
    preparer="clerk",
    balanced=True,
    steps=[
        ("submit", "clerk"),
        ("reject", "controller"),
        ("submit", "agent"),
        ("approve", "clerk"),
        ("post", "poster"),
    ],
)
@example(
    preparer="agent", balanced=False, steps=[("submit", "agent"), ("post", "poster")]
)
@settings(max_examples=150, deadline=None)
@given(
    preparer=st.sampled_from(PREPARERS),
    balanced=st.booleans(),
    steps=st.lists(
        st.tuples(st.sampled_from(STEPS), st.sampled_from(sorted(CAST))), max_size=12
    ),
)
def test_the_workflow_keeps_its_guarantees_whatever_is_attempted(
    database: Engine, preparer: str, balanced: bool, steps: list[tuple[str, str]]
) -> None:
    with _books(database) as (session, cast):
        credit = Decimal("500.00") if balanced else Decimal("450.00")
        entry = propose_journal_entry(
            session,
            actor=cast[preparer],
            entry_date=date(2026, 3, 15),
            description="Generated",
            lines=[
                LineInput("6200", debit=Decimal("500.00")),
                LineInput("1111", credit=credit),
            ],
        )
        session.flush()

        succeeded = 0
        for step, code in steps:
            before = (entry.status, len(workflow_history(session, entry)))
            try:
                _attempt(session, entry, step, cast[code])
            except (WorkflowError, JournalEntryError, ValueError):
                session.flush()
                assert (entry.status, len(workflow_history(session, entry))) == before
            else:
                succeeded += 1

        history = workflow_history(session, entry)
        assert len(history) == 1 + succeeded

        # One unbroken chain from the proposal to the current status.
        assert history[0].action is WorkflowAction.PROPOSE
        assert history[0].from_status is None
        for earlier, later in pairwise(history):
            assert later.from_status == earlier.to_status
        assert history[-1].to_status == entry.status.value

        # Approvals come from approvers who did not prepare the entry.
        preparers: set[str] = set()
        approved_since_submission = False
        for transition in history:
            if transition.action in (WorkflowAction.PROPOSE, WorkflowAction.SUBMIT):
                preparers.add(transition.actor.code)
                approved_since_submission = False
            if transition.action is WorkflowAction.APPROVE:
                assert transition.actor.code not in preparers
                assert P.APPROVER in transition.actor.permissions
                approved_since_submission = True
            if transition.action is WorkflowAction.POST:
                assert approved_since_submission
                assert P.POSTER in transition.actor.permissions

        # Nothing unbalanced reached the ledger, and the ledger balances.
        activity = posted_activity(session).values()
        assert sum(a.debits for a in activity) == sum(a.credits for a in activity)
        if not balanced:
            assert not entry.status.in_ledger

"""Property-based test: any change to any audit event, however made, is detected.

Hypothesis records a random log of actions, some succeeding and some refused, some
by an agent and some by the system, with random input, reasons, and evidence. Then,
for every event and every field in turn, it alters that one field with raw SQL,
beyond the reach of the ORM's guards, and ``verify_audit_log`` must name exactly
that event; restoring the field must make the log intact again. Every field of every
event is tried, so the coverage does not depend on what random search happens to
pick. Each example runs in a transaction that is rolled back.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from opensumma.db import Base, create_engine
from opensumma.workflow import (
    Actor,
    ActorType,
    audit_history,
    audited,
    create_actor,
    verify_audit_log,
)

# Each alteration always produces a value different from the original.
ALTERATIONS = {
    "reason": "reason = COALESCE(reason, '') || 'x'",
    "action": "action = action || 'x'",
    "input": """input = '{"altered": true}'""",
    "output": """output = '{"altered": true}'""",
    "result": (
        "result = CASE result WHEN 'SUCCEEDED' THEN 'REFUSED' ELSE 'SUCCEEDED' END"
    ),
    "object_id": "object_id = COALESCE(object_id, 0) + 1",
    "evidence": """evidence = '["altered=1"]'""",
    "occurred_at": "occurred_at = '2000-01-01 00:00:00.000000'",
}

words = st.text(alphabet="abcdefghij ", min_size=1, max_size=12).map(str.strip)
actions = st.fixed_dictionaries(
    {
        "name": st.sampled_from(["propose_journal_entry", "classify", "close_period"]),
        "by_agent": st.booleans(),
        "refused": st.booleans(),
        "input": st.dictionaries(words.filter(bool), st.integers() | words, max_size=3),
        "reason": st.none() | words.filter(bool),
        "evidence": st.lists(words.filter(bool).map(lambda w: f"ref={w}"), max_size=3),
        "object_id": st.none() | st.integers(min_value=1, max_value=99),
    }
)


@pytest.fixture(scope="module")
def database() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@contextmanager
def _log(engine: Engine) -> Iterator[tuple[Session, Actor]]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            agent = create_actor(
                session,
                code="agent",
                name="Agent",
                actor_type=ActorType.AGENT,
                permissions=["PROPOSER"],
            )
            yield session, agent
        transaction.rollback()


def _record(session: Session, agent: Actor, action: dict[str, object]) -> None:
    try:
        with audited(
            session,
            actor=agent if action["by_agent"] else None,
            action=str(action["name"]),
            input=action["input"],  # type: ignore[arg-type]
            reason=action["reason"],  # type: ignore[arg-type]
            evidence=action["evidence"],  # type: ignore[arg-type]
        ) as scope:
            scope.output = {"object_id": action["object_id"]}
            if action["refused"]:
                raise ValueError("refused for the test")
    except ValueError:
        pass


@settings(max_examples=30, deadline=None)
@given(log=st.lists(actions, min_size=1, max_size=6))
def test_any_altered_field_of_any_event_is_found(
    database: Engine, log: list[dict[str, object]]
) -> None:
    with _log(database) as (session, agent):
        for action in log:
            _record(session, agent, action)
        session.flush()
        assert verify_audit_log(session).is_intact
        connection = session.connection()

        for sequence in [event.sequence for event in audit_history(session)]:
            for field, alteration in ALTERATIONS.items():
                where = {"s": sequence}
                original: object = connection.execute(
                    text(f"SELECT {field} FROM audit_event WHERE sequence = :s"), where
                ).scalar_one()
                connection.execute(
                    text(f"UPDATE audit_event SET {alteration} WHERE sequence = :s"),
                    where,
                )
                session.expire_all()
                report = verify_audit_log(session)
                assert (report.broken_at, report.problem, field) == (
                    sequence,
                    "its content does not match its hash",
                    field,
                )

                connection.execute(
                    text(f"UPDATE audit_event SET {field} = :v WHERE sequence = :s"),
                    {"v": original, **where},
                )
                session.expire_all()
                assert verify_audit_log(session).is_intact

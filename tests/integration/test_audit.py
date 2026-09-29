"""The audit log: every action, allowed or refused, and every change made outside
the workflow, recorded append-only and hash-chained."""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise

import pytest
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.kernel import (
    Account,
    JournalEntry,
    JournalEntryError,
    LineInput,
    deactivate_account,
    get_account,
)
from opensumma.workflow import (
    Actor,
    ActorType,
    AuditEvent,
    AuditResult,
    ImmutableHistoryError,
    InvalidTransitionError,
    PermissionDeniedError,
    UnauditedWriteError,
    approve_journal_entry,
    audit_hash,
    audit_history,
    audited,
    create_actor,
    post_journal_entry,
    propose_journal_entry,
    reject_journal_entry,
    set_actor_permissions,
    submit_for_approval,
    verify_audit_log,
    void_journal_entry,
)

MARCH_15 = date(2026, 3, 15)
RENT = [
    LineInput("6200", debit=Decimal("2500.00"), dimensions={"LOCATION": "HQ"}),
    LineInput("1111", credit=Decimal("2500.00")),
]


def _mark(session: Session) -> int:
    """How many events the log holds now, so a test can look only at what follows."""
    session.commit()
    return len(audit_history(session))


def _since(session: Session, mark: int) -> list[AuditEvent]:
    session.commit()
    return audit_history(session)[mark:]


def _summary(events: list[AuditEvent]) -> list[tuple[object, ...]]:
    return [
        (e.action, None if e.actor is None else e.actor.code, e.result.value)
        for e in events
    ]


def _propose(session: Session, actor: Actor, **extra: object) -> JournalEntry:
    return propose_journal_entry(
        session,
        actor=actor,
        entry_date=MARCH_15,
        description="March rent",
        lines=RENT,
        **extra,  # type: ignore[arg-type]
    )


def test_every_workflow_action_is_audited(
    books: Session, agent: Actor, controller: Actor, poster: Actor
) -> None:
    mark = _mark(books)
    entry = _propose(books, agent)
    submit_for_approval(books, entry, actor=agent)
    approve_journal_entry(books, entry, actor=controller, reason="Matches the lease")
    post_journal_entry(books, entry, actor=poster)
    events = _since(books, mark)

    assert _summary(events) == [
        ("propose_journal_entry", "je-agent", "SUCCEEDED"),
        ("submit_for_approval", "je-agent", "SUCCEEDED"),
        ("approve_journal_entry", "controller", "SUCCEEDED"),
        ("post_journal_entry", "poster", "SUCCEEDED"),
    ]
    assert {(e.object_type, e.object_id) for e in events} == {
        ("journal_entry", entry.id)
    }
    assert [e.output["status"] for e in events] == [
        "PROPOSED",
        "PENDING_APPROVAL",
        "APPROVED",
        "POSTED",
    ]
    assert events[2].reason == "Matches the lease"
    assert [e.actor_type for e in events] == [
        ActorType.AGENT,
        ActorType.AGENT,
        ActorType.HUMAN,
        ActorType.SYSTEM,
    ]
    assert audit_history(books, subject=entry) == events


def test_a_proposal_records_its_input_exactly(books: Session, agent: Actor) -> None:
    mark = _mark(books)
    _propose(books, agent)
    (event,) = _since(books, mark)

    assert event.input == {
        "entry_date": "2026-03-15",
        "description": "March rent",
        "lines": [
            {
                "account": "6200",
                "debit": "2500.00",
                "credit": "0.00",
                "memo": None,
                "dimensions": {"LOCATION": "HQ"},
            },
            {
                "account": "1111",
                "debit": "0.00",
                "credit": "2500.00",
                "memo": None,
                "dimensions": {},
            },
        ],
        "accounting_object_id": None,
    }


def test_reason_and_evidence_are_stored_as_the_specification_shows(
    books: Session, agent: Actor
) -> None:
    mark = _mark(books)
    _propose(
        books,
        agent,
        reason="Historical AWS transactions were classified to account 6100.",
        evidence=["vendor_id=42", "historical_account=6100"],
    )
    (event,) = _since(books, mark)

    assert event.action == "propose_journal_entry"
    assert event.actor_type is ActorType.AGENT
    assert (
        event.reason == "Historical AWS transactions were classified to account 6100."
    )
    assert event.evidence == ["vendor_id=42", "historical_account=6100"]


def test_refused_attempts_are_audited_and_change_nothing_else(
    books: Session, agent: Actor, controller: Actor
) -> None:
    entry = _propose(books, agent)
    mark = _mark(books)

    # The state is checked first: nothing is approved before it is submitted.
    with pytest.raises(InvalidTransitionError):
        approve_journal_entry(books, entry, actor=agent)
    with pytest.raises(InvalidTransitionError):
        reject_journal_entry(books, entry, actor=controller, reason="Wrong account")
    with pytest.raises(ValueError, match="reason is required"):
        void_journal_entry(books, entry, actor=agent, reason=" ")
    events = _since(books, mark)

    assert _summary(events) == [
        ("approve_journal_entry", "je-agent", "REFUSED"),
        ("reject_journal_entry", "controller", "REFUSED"),
        ("void_journal_entry", "je-agent", "REFUSED"),
    ]
    assert events[0].output == {
        "error": "InvalidTransitionError",
        "message": "cannot approve from PROPOSED; allowed from PENDING_APPROVAL",
        "action": "approve",
        "status": "PROPOSED",
    }
    assert events[1].output["error"] == "InvalidTransitionError"
    assert events[2].output["error"] == "ValueError"
    assert events[1].reason == "Wrong account"  # kept, though the action was refused
    assert entry.status is kernel.JournalEntryStatus.PROPOSED


def test_a_permission_refusal_names_the_permission(
    books: Session, agent: Actor, clerk: Actor
) -> None:
    entry = _propose(books, clerk)
    submit_for_approval(books, entry, actor=clerk)
    mark = _mark(books)
    with pytest.raises(PermissionDeniedError):
        approve_journal_entry(books, entry, actor=agent)
    (event,) = _since(books, mark)

    assert event.result is AuditResult.REFUSED
    assert event.output["error"] == "PermissionDeniedError"
    assert event.output["permission"] == "APPROVER"


def test_a_refusal_by_the_kernel_records_its_issue_codes(
    books: Session, agent: Actor
) -> None:
    entry = propose_journal_entry(
        books,
        actor=agent,
        entry_date=MARCH_15,
        description="Unbalanced",
        lines=[
            LineInput("6200", debit=Decimal("2500.00")),
            LineInput("1111", credit=Decimal("2400.00")),
        ],
    )
    mark = _mark(books)
    with pytest.raises(JournalEntryError):
        submit_for_approval(books, entry, actor=agent)
    (event,) = _since(books, mark)

    assert event.result is AuditResult.REFUSED
    assert [issue["code"] for issue in event.output["issues"]] == ["UNBALANCED"]


def test_a_proposal_the_kernel_cannot_record_is_audited_without_an_object(
    books: Session, agent: Actor
) -> None:
    mark = _mark(books)
    with pytest.raises(JournalEntryError):
        propose_journal_entry(
            books,
            actor=agent,
            entry_date=MARCH_15,
            description="Unknown account",
            lines=[
                LineInput("9999", debit=Decimal("1.00")),
                LineInput("1111", credit=Decimal("1.00")),
            ],
        )
    (event,) = _since(books, mark)
    assert (event.object_type, event.object_id) == (None, None)
    assert event.output["issues"][0]["code"] == "UNKNOWN_ACCOUNT"


def test_bad_evidence_or_an_overlong_reason_is_refused_and_still_audited(
    books: Session, agent: Actor
) -> None:
    mark = _mark(books)
    with pytest.raises(TypeError):
        _propose(books, agent, evidence="vendor_id=42")
    with pytest.raises(ValueError, match="limited to 500"):
        _propose(books, agent, reason="because " * 100)
    events = _since(books, mark)

    assert [e.result for e in events] == [AuditResult.REFUSED] * 2
    assert events[0].evidence == []
    assert events[1].reason is None  # too long to be concise, so not kept
    assert books.query(JournalEntry).count() == 0


def test_changes_outside_the_workflow_are_captured_as_the_system(
    books: Session,
) -> None:
    mark = _mark(books)
    entry = kernel.create_journal_entry(
        books, entry_date=MARCH_15, description="Rent", lines=RENT
    )
    books.commit()
    kernel.post_journal_entry(books, entry)
    deactivate_account(get_account(books, "6800"))
    events = _since(books, mark)

    assert all(e.actor is None and e.actor_type is ActorType.SYSTEM for e in events)
    assert [e.action for e in events] == [
        "create_journal_entry",
        "create_journal_line",
        "create_journal_line",
        "create_journal_line_dimension",
        "update_journal_entry",
        "update_account",
    ]
    created = events[0]
    assert (created.object_type, created.object_id) == ("journal_entry", entry.id)
    assert created.input["values"]["status"] == "DRAFT"
    assert created.input["values"]["description"] == "Rent"
    assert events[1].input["values"]["debit"] == "2500.00"

    posting = events[4].input["changes"]
    assert posting["status"] == {"from": "DRAFT", "to": "POSTED"}
    assert posting["posted_at"]["from"] is None
    assert events[5].input == {"changes": {"is_active": {"from": True, "to": False}}}


def test_granting_and_revoking_permissions_is_captured(books: Session) -> None:
    mark = _mark(books)
    bot = create_actor(
        books, code="bot", name="Bot", actor_type="AGENT", permissions=["PROPOSER"]
    )
    books.commit()
    set_actor_permissions(bot, ["APPROVER"])
    events = _since(books, mark)

    assert [
        (e.action, e.input.get("values", {}).get("permission")) for e in events
    ] == [
        ("create_actor", None),
        ("create_actor_permission", "PROPOSER"),
        ("create_actor_permission", "APPROVER"),
        ("delete_actor_permission", "PROPOSER"),
    ]


def test_changes_made_by_an_audited_action_are_not_captured_again(
    books: Session, agent: Actor
) -> None:
    mark = _mark(books)
    _propose(books, agent)
    assert [e.action for e in _since(books, mark)] == ["propose_journal_entry"]


def test_changes_pending_before_an_action_are_captured_on_their_own(
    books: Session, agent: Actor
) -> None:
    mark = _mark(books)
    get_account(books, "6800").name = "Insurance"
    _propose(books, agent)
    assert _summary(_since(books, mark)) == [
        ("update_account", None, "SUCCEEDED"),
        ("propose_journal_entry", "je-agent", "SUCCEEDED"),
    ]


def test_trusted_code_can_audit_a_batch_as_one_action(books: Session) -> None:
    mark = _mark(books)
    with audited(
        books,
        actor=None,
        action="generate_dataset",
        input={"company": "acme", "seed": 42},
        evidence=["generator=v1"],
    ) as batch:
        for n in range(3):
            kernel.post_journal_entry(
                books,
                kernel.create_journal_entry(
                    books, entry_date=MARCH_15, description=f"Rent {n}", lines=RENT
                ),
            )
        batch.output = {"entries": 3}
    (event,) = _since(books, mark)

    assert (event.action, event.actor, event.actor_type) == (
        "generate_dataset",
        None,
        ActorType.SYSTEM,
    )
    assert event.output == {"entries": 3}


def test_only_the_system_acts_without_an_actor(books: Session) -> None:
    books.add(
        AuditEvent(
            actor_type=ActorType.AGENT,
            action="forged",
            result=AuditResult.SUCCEEDED,
        )
    )
    with pytest.raises(IntegrityError):
        books.flush()


def test_audit_events_are_never_changed_or_deleted(
    books: Session, agent: Actor
) -> None:
    mark = _mark(books)
    _propose(books, agent)
    (event,) = _since(books, mark)

    event.reason = "rewritten"
    with pytest.raises(ImmutableHistoryError):
        books.flush()
    books.rollback()

    books.delete(event)
    with pytest.raises(ImmutableHistoryError):
        books.flush()
    books.rollback()

    for statement in (
        update(AuditEvent).values(reason="rewritten"),
        delete(AuditEvent),
        insert(AuditEvent).values(action="forged"),
    ):
        with pytest.raises(ImmutableHistoryError, match="bulk"):
            books.execute(statement)


def test_no_record_is_written_in_bulk_behind_the_audit_log(books: Session) -> None:
    for statement in (
        insert(Account).values(code="7000", name="Other", account_type="EXPENSE"),
        update(Account).values(name="Renamed"),
        delete(Account),
    ):
        with pytest.raises(UnauditedWriteError):
            books.execute(statement)


def test_events_are_numbered_and_chained(books: Session, agent: Actor) -> None:
    _propose(books, agent)
    books.commit()
    events = audit_history(books)

    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert events[0].previous_hash is None
    for earlier, later in pairwise(events):
        assert later.previous_hash == earlier.hash
    assert all(e.hash == audit_hash(e) for e in events)

    report = verify_audit_log(books)
    assert report.is_intact
    assert report.events_checked == len(events)
    assert report.head_hash == events[-1].hash


def _tamper(session: Session, sql: str, **params: object) -> None:
    session.commit()
    session.connection().execute(text(sql), params)
    session.commit()
    session.expire_all()


def test_an_event_changed_behind_the_orm_is_detected(
    books: Session, agent: Actor
) -> None:
    _propose(books, agent, reason="Rent is due")
    target = audit_history(books, action="propose_journal_entry")[0].sequence

    _tamper(
        books,
        "UPDATE audit_event SET reason = 'Approved by the CFO' WHERE sequence = :s",
        s=target,
    )
    report = verify_audit_log(books)
    assert not report.is_intact
    assert report.broken_at == target
    assert report.problem == "its content does not match its hash"


def test_rehashing_a_changed_event_breaks_the_next_link(
    books: Session, agent: Actor
) -> None:
    _propose(books, agent)
    _propose(books, agent)  # so something follows the event that is altered
    target = audit_history(books, action="propose_journal_entry")[0]
    forged = AuditEvent(
        sequence=target.sequence,
        occurred_at=target.occurred_at,
        actor_type=target.actor_type,
        actor_id=target.actor_id,
        action=target.action,
        object_type=target.object_type,
        object_id=target.object_id,
        input=target.input,
        output={"status": "POSTED"},
        result=target.result,
        reason=target.reason,
        evidence=target.evidence,
        previous_hash=target.previous_hash,
    )

    _tamper(
        books,
        "UPDATE audit_event SET output = :o, hash = :h WHERE sequence = :s",
        o='{"status": "POSTED"}',
        h=audit_hash(forged),
        s=target.sequence,
    )
    report = verify_audit_log(books)
    assert report.broken_at == target.sequence + 1
    assert report.problem == "it does not carry the hash of the event before it"


def test_rewriting_an_event_and_relinking_the_next_is_still_detected(
    books: Session, agent: Actor
) -> None:
    """Each hash covers the previous one, so relinking a forgery changes the next
    event's content: the forger would have to rewrite every event after it."""
    _propose(books, agent)
    _propose(books, agent)
    target = audit_history(books, action="propose_journal_entry")[0]
    forged = AuditEvent(
        sequence=target.sequence,
        occurred_at=target.occurred_at,
        actor_type=target.actor_type,
        actor_id=target.actor_id,
        action=target.action,
        object_type=target.object_type,
        object_id=target.object_id,
        input=target.input,
        output={"status": "POSTED"},
        result=target.result,
        reason=target.reason,
        evidence=target.evidence,
        previous_hash=target.previous_hash,
    )
    forged_hash = audit_hash(forged)

    _tamper(
        books,
        "UPDATE audit_event SET output = :o, hash = :h WHERE sequence = :s",
        o='{"status": "POSTED"}',
        h=forged_hash,
        s=target.sequence,
    )
    _tamper(
        books,
        "UPDATE audit_event SET previous_hash = :h WHERE sequence = :s",
        h=forged_hash,
        s=target.sequence + 1,
    )
    report = verify_audit_log(books)
    assert report.broken_at == target.sequence + 1
    assert report.problem == "its content does not match its hash"


def test_a_removed_event_is_detected(books: Session, agent: Actor) -> None:
    _propose(books, agent)
    _propose(books, agent)
    target = audit_history(books, action="propose_journal_entry")[0].sequence

    _tamper(books, "DELETE FROM audit_event WHERE sequence = :s", s=target)
    report = verify_audit_log(books)
    assert report.broken_at == target
    assert report.problem == f"expected event {target}, found event {target + 1}"


def test_an_empty_log_is_intact(session: Session) -> None:
    report = verify_audit_log(session)
    assert report.is_intact
    assert (report.events_checked, report.head_hash) == (0, None)


def test_audit_history_filters(books: Session, agent: Actor, controller: Actor) -> None:
    entry = _propose(books, agent)
    submit_for_approval(books, entry, actor=agent)
    with pytest.raises(PermissionDeniedError):
        approve_journal_entry(books, entry, actor=agent)
    approve_journal_entry(books, entry, actor=controller)
    books.commit()

    assert [e.action for e in audit_history(books, actor=controller)] == [
        "approve_journal_entry"
    ]
    assert [e.actor.code for e in audit_history(books, result="REFUSED")] == [  # type: ignore[union-attr]
        "je-agent"
    ]
    assert len(audit_history(books, action="approve_journal_entry")) == 2
    assert len(audit_history(books, subject=entry)) == 4


def test_the_occurrence_time_is_utc(books: Session, agent: Actor) -> None:
    mark = _mark(books)
    _propose(books, agent)
    (event,) = _since(books, mark)
    assert event.occurred_at.tzinfo is UTC
    assert event.occurred_at <= datetime.now(UTC)


def test_a_line_removed_from_a_draft_is_captured(books: Session) -> None:
    """Removed lines are deleted as orphans during the flush, not by the caller."""
    draft = kernel.create_journal_entry(
        books, entry_date=MARCH_15, description="Rent", lines=RENT
    )
    mark = _mark(books)
    draft.lines.pop(0)  # the line carrying LOCATION=HQ
    events = _since(books, mark)

    assert [e.action for e in events] == [
        "delete_journal_line_dimension",
        "delete_journal_line",
    ]
    assert events[1].input["values"]["debit"] == "2500.00"
    assert events[1].input["values"]["account_id"] == get_account(books, "6200").id

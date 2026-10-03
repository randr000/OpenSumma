"""Phase 6 acceptance: the audit log.

On a database created by the migrations, an AI agent proposes the entry from the
specification's own example, with its reason and evidence; it tries, and fails, to
approve its own work; a human controller approves; a posting service posts. Trusted
code outside the workflow also opens the company's books and posts the owner's
investment, and a period is closed. Then someone edits the log behind the ORM.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.db import init_db
from opensumma.kernel import (
    JournalEntry,
    LineInput,
    create_calendar_year_periods,
    get_period,
    ledger_lines,
    seed_chart_of_accounts,
    seed_dimensions,
)
from opensumma.objects import seed_counterparties
from opensumma.workflow import (
    Actor,
    ActorType,
    AuditEvent,
    AuditResult,
    ImmutableHistoryError,
    InvalidTransitionError,
    Permission,
    approve_journal_entry,
    audit_history,
    close_period,
    create_actor,
    post_journal_entry,
    propose_journal_entry,
    submit_for_approval,
    verify_audit_log,
)

REASON = "Historical AWS transactions were classified to account 6100."
EVIDENCE = ["vendor_id=42", "historical_account=6100"]


@dataclass
class Month:
    session: Session
    agent: Actor
    controller: Actor
    posting: Actor
    admin: Actor
    capital: JournalEntry
    proposal: JournalEntry


@pytest.fixture
def month(database_url: str, engine: Engine) -> Iterator[Month]:
    init_db(database_url)
    with Session(engine) as session:
        # Trusted setup, outside the workflow: captured as the system.
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        seed_counterparties(session)
        create_calendar_year_periods(session, 2026)
        capital = kernel.create_journal_entry(
            session,
            entry_date=date(2026, 3, 1),
            description="Owner investment",
            lines=[
                LineInput("1111", Decimal("10000.00")),
                LineInput("3100", Decimal("-10000.00")),
            ],
        )
        kernel.post_journal_entry(session, capital)

        def actor(code: str, kind: ActorType, *permissions: Permission) -> Actor:
            return create_actor(
                session, code=code, name=code, actor_type=kind, permissions=permissions
            )

        agent = actor("ap-agent", ActorType.AGENT, Permission.PROPOSER)
        controller = actor("maria", ActorType.HUMAN, Permission.APPROVER)
        posting = actor("posting-service", ActorType.SYSTEM, Permission.POSTER)
        admin = actor("cfo", ActorType.HUMAN, Permission.ADMIN)

        proposal = propose_journal_entry(
            session,
            actor=agent,
            entry_date=date(2026, 3, 15),
            description="AWS, March",
            lines=[
                LineInput("6100", Decimal("120.50")),
                LineInput("2110", Decimal("-120.50")),
            ],
            reason=REASON,
            evidence=EVIDENCE,
        )
        submit_for_approval(session, proposal, actor=agent)
        with pytest.raises(InvalidTransitionError):
            post_journal_entry(session, proposal, actor=agent)  # not approved yet
        approve_journal_entry(session, proposal, actor=controller, reason="Checked")
        post_journal_entry(session, proposal, actor=posting)
        close_period(session, get_period(session, "2026-01"), actor=admin)
        session.commit()
        yield Month(session, agent, controller, posting, admin, capital, proposal)


def test_audit_events_exist(month: Month) -> None:
    event = audit_history(month.session, action="propose_journal_entry")[0]

    # Every field the specification lists, and the concise reason.
    assert event.occurred_at.tzinfo is not None  # timestamp
    assert event.actor_type is ActorType.AGENT
    assert event.actor_id == month.agent.id
    assert event.action == "propose_journal_entry"
    assert (event.object_type, event.object_id) == ("journal_entry", month.proposal.id)
    assert event.input["description"] == "AWS, March"
    assert event.input["lines"][0]["amount"] == "120.50"
    assert event.output == {"status": "PROPOSED"}
    assert event.result is AuditResult.SUCCEEDED
    assert event.evidence == EVIDENCE
    assert event.reason == REASON


def test_human_agent_and_system_actors_are_distinguishable(month: Month) -> None:
    trail = audit_history(month.session, subject=month.proposal)
    assert [
        (
            e.action,
            e.actor_type.value,
            e.actor.code if e.actor else None,
            e.result.value,
        )
        for e in trail
    ] == [
        ("propose_journal_entry", "AGENT", "ap-agent", "SUCCEEDED"),
        ("submit_for_approval", "AGENT", "ap-agent", "SUCCEEDED"),
        ("post_journal_entry", "AGENT", "ap-agent", "REFUSED"),
        ("approve_journal_entry", "HUMAN", "maria", "SUCCEEDED"),
        ("post_journal_entry", "SYSTEM", "posting-service", "SUCCEEDED"),
    ]

    # Trusted code acting as the system itself has no actor at all.
    opening = audit_history(month.session, subject=month.capital)
    assert {(e.actor_type, e.actor_id) for e in opening} == {(ActorType.SYSTEM, None)}


def test_accounting_mutations_create_audit_events(month: Month) -> None:
    session = month.session
    # Through the workflow: the proposal's whole life, and the period close.
    close = audit_history(session, action="close_period")
    assert [(e.actor.code, e.output) for e in close] == [  # type: ignore[union-attr]
        ("cfo", {"status": "CLOSED"})
    ]

    # Outside it: the owner's investment was created and posted by trusted code.
    # Posting validated it, which flushed it as a draft first; the log shows both.
    opening = audit_history(session, subject=month.capital)
    assert [e.action for e in opening] == [
        "create_journal_entry",
        "update_journal_entry",
    ]
    assert opening[0].input["values"]["status"] == "DRAFT"
    assert opening[1].input["changes"]["status"] == {"from": "DRAFT", "to": "POSTED"}

    # Every entry in the ledger has an audit trail.
    for entry_id in {line.entry_id for line in ledger_lines(session)}:
        entry = session.get(JournalEntry, entry_id)
        assert audit_history(session, subject=entry)

    # So does the chart of accounts the books were opened with.
    assert len(audit_history(session, action="create_account")) == 41


def test_audit_records_cannot_be_silently_modified(month: Month) -> None:
    session = month.session
    assert verify_audit_log(session).is_intact
    event = audit_history(session, action="approve_journal_entry")[0]

    # Not through the ORM...
    event.reason = "Approved without checking"
    with pytest.raises(ImmutableHistoryError):
        session.flush()
    session.rollback()

    # ...and not silently behind it.
    session.connection().execute(
        text(
            "UPDATE audit_event SET reason = 'Approved by the CFO' WHERE sequence = :s"
        ),
        {"s": event.sequence},
    )
    session.commit()
    session.expire_all()
    report = verify_audit_log(session)
    assert not report.is_intact
    assert report.broken_at == event.sequence


def test_evidence_references_can_be_stored(month: Month) -> None:
    session = month.session
    session.expire_all()  # read back from the database, not from memory
    stored = session.get(
        AuditEvent, audit_history(session, action="propose_journal_entry")[0].id
    )
    assert stored is not None
    assert stored.evidence == ["vendor_id=42", "historical_account=6100"]
    assert stored.reason == REASON

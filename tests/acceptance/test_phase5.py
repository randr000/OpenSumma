"""Phase 5 acceptance: the workflow engine.

Four actors keep a small company's books in March, on a database created by the
migrations:

    ap-agent         AGENT   READ_ONLY, PROPOSER   reads documents and proposes
    maria            HUMAN   READ_ONLY, APPROVER   reviews and approves
    posting-service  SYSTEM  POSTER                posts what is approved
    cfo              HUMAN   ADMIN                 closes periods

The agent turns an emailed Stratus bill into a proposal, which Maria approves and
the posting service posts: Dr 5200 Hosting 310.00, Cr 2110 AP 310.00. It proposes an
invoice to Helio at 1,800.00 against product revenue; Maria rejects it, because the
work was consulting, and the corrected proposal (Dr 1120, Cr 4200) is approved and
posted. The trial balance at Mar 31 is therefore 1120 1,800.00 Dr; 2110 310.00 Cr;
4200 1,800.00 Cr; 5200 310.00 Dr; totals 2,110.00.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from opensumma.db import init_db
from opensumma.kernel import (
    JournalEntry,
    JournalEntryStatus,
    LineInput,
    PeriodStatus,
    create_calendar_year_periods,
    get_period,
    seed_chart_of_accounts,
    seed_dimensions,
    trial_balance,
)
from opensumma.objects import (
    AccountingObject,
    AccountingObjectStatus,
    accounting_impact,
    seed_counterparties,
)
from opensumma.workflow import (
    ACCOUNTING_OBJECT_WORKFLOW,
    ACCOUNTING_PERIOD_WORKFLOW,
    JOURNAL_ENTRY_WORKFLOW,
    Actor,
    ActorType,
    InvalidTransitionError,
    PendingEntriesError,
    PeriodSequenceError,
    Permission,
    PermissionDeniedError,
    SegregationOfDutiesError,
    Transition,
    WorkflowTransition,
    approve_journal_entry,
    classify_accounting_object,
    close_period,
    create_actor,
    extract_accounting_object,
    observe_accounting_object,
    post_journal_entry,
    propose_journal_entry,
    reject_journal_entry,
    reopen_period,
    set_actor_permissions,
    submit_for_approval,
    validate_journal_entry,
    void_journal_entry,
    workflow_history,
)

MAR_31 = date(2026, 3, 31)


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 3, day, hour, tzinfo=UTC)


@dataclass
class March:
    session: Session
    agent: Actor
    maria: Actor
    posting: Actor
    cfo: Actor
    bill: AccountingObject
    bill_entry: JournalEntry
    invoice: AccountingObject
    rejected: JournalEntry
    corrected: JournalEntry


def _transitions(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(WorkflowTransition)) or 0


def _steps(session: Session, subject: object) -> list[tuple[str, str, str, str]]:
    return [
        (t.action.value, t.from_status or "-", t.to_status, t.actor.code)
        for t in workflow_history(session, subject)  # type: ignore[arg-type]
    ]


@pytest.fixture
def march(database_url: str, engine: Engine) -> Iterator[March]:
    init_db(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        seed_counterparties(session)
        create_calendar_year_periods(session, 2026)

        def actor(code: str, kind: ActorType, *permissions: Permission) -> Actor:
            return create_actor(
                session,
                code=code,
                name=code,
                actor_type=kind,
                permissions=permissions,
            )

        agent = actor(
            "ap-agent", ActorType.AGENT, Permission.READ_ONLY, Permission.PROPOSER
        )
        maria = actor(
            "maria", ActorType.HUMAN, Permission.READ_ONLY, Permission.APPROVER
        )
        posting = actor("posting-service", ActorType.SYSTEM, Permission.POSTER)
        cfo = actor("cfo", ActorType.HUMAN, Permission.ADMIN)

        # The agent reads an emailed bill, extracts it, and settles its vendor.
        bill = observe_accounting_object(
            session,
            actor=agent,
            object_type="vendor_bill",
            occurred_at=_at(3),
            source="email",
            data={"raw": "STRATUS CLOUD HOSTING / INV-88 / due 2026-04-02 / 310.00"},
        )
        extract_accounting_object(
            session,
            bill,
            actor=agent,
            data={"invoice_number": "INV-88", "due": "2026-04-02", "amount": "310.00"},
        )
        classify_accounting_object(
            session, bill, actor=agent, counterparty="V-STRATUS", reason="Letterhead"
        )
        bill_entry = propose_journal_entry(
            session,
            actor=agent,
            entry_date=date(2026, 3, 3),
            description="Stratus INV-88, March hosting",
            lines=[
                LineInput("5200", debit=Decimal("310.00")),
                LineInput("2110", credit=Decimal("310.00")),
            ],
            accounting_object=bill,
            reason="Stratus bills have been hosting (5200) for twelve months",
        )
        assert validate_journal_entry(session, bill_entry, actor=agent) == []
        submit_for_approval(session, bill_entry, actor=agent)
        approve_journal_entry(session, bill_entry, actor=maria, reason="Checked")
        post_journal_entry(session, bill_entry, actor=posting)

        # The agent proposes an invoice against the wrong revenue account.
        invoice = observe_accounting_object(
            session,
            actor=agent,
            object_type="customer_invoice",
            occurred_at=_at(12),
            source="billing",
            counterparty="C-HELIO",
            data={"invoice_number": "INV-C-7", "amount": "1800.00"},
        )
        classify_accounting_object(session, invoice, actor=agent)

        def invoice_entry(revenue: str) -> JournalEntry:
            return propose_journal_entry(
                session,
                actor=agent,
                entry_date=date(2026, 3, 12),
                description="Helio INV-C-7",
                lines=[
                    LineInput("1120", debit=Decimal("1800.00")),
                    LineInput(revenue, credit=Decimal("1800.00")),
                ],
                accounting_object=invoice,
            )

        rejected = invoice_entry("4100")
        submit_for_approval(session, rejected, actor=agent)
        reject_journal_entry(
            session, rejected, actor=maria, reason="Consulting, not product: 4200"
        )
        void_journal_entry(
            session, rejected, actor=agent, reason="Replaced by a corrected proposal"
        )
        corrected = invoice_entry("4200")
        submit_for_approval(session, corrected, actor=agent)
        approve_journal_entry(session, corrected, actor=maria)
        post_journal_entry(session, corrected, actor=posting)
        session.commit()

        yield March(
            session=session,
            agent=agent,
            maria=maria,
            posting=posting,
            cfo=cfo,
            bill=bill,
            bill_entry=bill_entry,
            invoice=invoice,
            rejected=rejected,
            corrected=corrected,
        )


def test_workflow_states_exist(march: March) -> None:
    def states(workflow: tuple[Transition, ...]) -> set[StrEnum]:
        return {t.target for t in workflow} | {
            s for t in workflow for s in t.sources if s is not None
        }

    assert states(JOURNAL_ENTRY_WORKFLOW) == set(JournalEntryStatus)
    assert states(ACCOUNTING_OBJECT_WORKFLOW) == set(AccountingObjectStatus)
    assert states(ACCOUNTING_PERIOD_WORKFLOW) == set(PeriodStatus)

    # The states are the ones the subjects actually pass through.
    assert march.bill.status is AccountingObjectStatus.CLASSIFIED
    assert march.bill_entry.status is JournalEntryStatus.POSTED
    assert march.rejected.status is JournalEntryStatus.VOIDED


def test_state_transitions_are_validated(march: March) -> None:
    session = march.session
    history_before = _transitions(session)

    with pytest.raises(InvalidTransitionError, match="cannot post from POSTED"):
        post_journal_entry(session, march.bill_entry, actor=march.posting)
    with pytest.raises(InvalidTransitionError, match="cannot submit from VOIDED"):
        submit_for_approval(session, march.rejected, actor=march.agent)
    with pytest.raises(InvalidTransitionError, match="cannot approve from POSTED"):
        approve_journal_entry(session, march.corrected, actor=march.maria)

    session.flush()
    assert _transitions(session) == history_before
    assert march.bill_entry.status is JournalEntryStatus.POSTED


def test_agent_proposals_can_enter_the_workflow(march: March) -> None:
    session = march.session
    assert march.agent.actor_type is ActorType.AGENT
    assert _steps(session, march.bill) == [
        ("observe", "-", "OBSERVED", "ap-agent"),
        ("extract", "OBSERVED", "EXTRACTED", "ap-agent"),
        ("classify", "EXTRACTED", "CLASSIFIED", "ap-agent"),
    ]
    proposal = workflow_history(session, march.bill_entry)[0]
    assert (proposal.action.value, proposal.actor.code) == ("propose", "ap-agent")
    assert proposal.reason == "Stratus bills have been hosting (5200) for twelve months"
    # The proposal is linked to the bill it records.
    assert [e.entry_id for e in accounting_impact(session, march.bill).entries] == [
        march.bill_entry.id
    ]

    # A normal agent proposes; it neither approves nor posts.
    draft = propose_journal_entry(
        session,
        actor=march.agent,
        entry_date=MAR_31,
        description="Accrue March electricity",
        lines=[
            LineInput("6200", debit=Decimal("90.00")),
            LineInput("2120", credit=Decimal("90.00")),
        ],
    )
    submit_for_approval(session, draft, actor=march.agent)
    with pytest.raises(PermissionDeniedError, match="APPROVER"):
        approve_journal_entry(session, draft, actor=march.agent)
    assert draft.status is JournalEntryStatus.PENDING_APPROVAL


def test_human_approval_can_be_represented(march: March) -> None:
    session = march.session
    approval = workflow_history(session, march.bill_entry)[2]
    assert approval.action.value == "approve"
    assert approval.actor.code == "maria"
    assert approval.actor.actor_type is ActorType.HUMAN
    assert approval.reason == "Checked"

    assert _steps(session, march.rejected) == [
        ("propose", "-", "PROPOSED", "ap-agent"),
        ("submit", "PROPOSED", "PENDING_APPROVAL", "ap-agent"),
        ("reject", "PENDING_APPROVAL", "PROPOSED", "maria"),
        ("void", "PROPOSED", "VOIDED", "ap-agent"),
    ]
    assert workflow_history(session, march.rejected)[2].reason == (
        "Consulting, not product: 4200"
    )

    # Whoever prepares an entry never approves it, even when allowed to approve.
    set_actor_permissions(march.maria, ["PROPOSER", "APPROVER"])
    own = propose_journal_entry(
        session,
        actor=march.maria,
        entry_date=MAR_31,
        description="Maria's own adjustment",
        lines=[
            LineInput("6700", debit=Decimal("15.00")),
            LineInput("1111", credit=Decimal("15.00")),
        ],
    )
    submit_for_approval(session, own, actor=march.maria)
    with pytest.raises(SegregationOfDutiesError):
        approve_journal_entry(session, own, actor=march.maria)


def test_posting_requires_appropriate_state_and_permission(march: March) -> None:
    session = march.session
    pending = propose_journal_entry(
        session,
        actor=march.agent,
        entry_date=MAR_31,
        description="Office supplies",
        lines=[
            LineInput("6700", debit=Decimal("45.00")),
            LineInput("1111", credit=Decimal("45.00")),
        ],
    )
    submit_for_approval(session, pending, actor=march.agent)
    with pytest.raises(InvalidTransitionError, match="allowed from APPROVED"):
        post_journal_entry(session, pending, actor=march.posting)
    approve_journal_entry(session, pending, actor=march.maria)
    with pytest.raises(PermissionDeniedError, match="POSTER"):
        post_journal_entry(session, pending, actor=march.maria)

    # Only what was posted is in the ledger: the approved entry is not, yet.
    report = trial_balance(session, as_of=MAR_31)
    assert [
        (line.account_code, str(line.debit), str(line.credit)) for line in report.lines
    ] == [
        ("1120", "1800.00", "0.00"),
        ("2110", "0.00", "310.00"),
        ("4200", "0.00", "1800.00"),
        ("5200", "310.00", "0.00"),
    ]
    assert report.total_debits == report.total_credits == Decimal("2110.00")

    # Closing March is an admin's call, in order, and not over unposted entries.
    march_period = get_period(session, "2026-03")
    with pytest.raises(PermissionDeniedError, match="ADMIN"):
        close_period(session, march_period, actor=march.maria)
    for code in ("2026-01", "2026-02"):
        close_period(session, get_period(session, code), actor=march.cfo)
    with pytest.raises(PendingEntriesError) as caught:
        close_period(session, march_period, actor=march.cfo)
    assert caught.value.entry_ids == (pending.id,)

    post_journal_entry(session, pending, actor=march.posting)
    close_period(session, march_period, actor=march.cfo)
    assert march_period.status is PeriodStatus.CLOSED
    with pytest.raises(PeriodSequenceError):
        reopen_period(
            session, get_period(session, "2026-02"), actor=march.cfo, reason="Late"
        )

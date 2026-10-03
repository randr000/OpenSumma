"""The MCP interface's mutating tools: the workflow through MCP, the controls it
keeps, and how refusals are answered and audited."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.orm import Session

from opensumma.db import create_engine
from opensumma.workflow import (
    ActorType,
    AuditResult,
    Permission,
    audit_history,
    create_actor,
    issue_api_key,
)

if TYPE_CHECKING:  # conftest is not importable while the tests run
    from conftest import McpCaller, McpServers

AWS: dict[str, Any] = {
    "entry_date": "2026-03-15",
    "description": "AWS, March",
    "lines": [
        {"account": "6100", "amount": "120.50", "dimensions": {"DEPARTMENT": "ENG"}},
        {"account": "2110", "amount": "-120.50"},
    ],
}


def _propose(agent: McpCaller, **changes: Any) -> int:
    entry_id: int = agent("propose_journal_entry", **{**AWS, **changes})["id"]
    return entry_id


def _audited(url: str, action: str) -> list[tuple[str | None, str]]:
    """What the audit log holds for ``action``, read afresh from the database."""
    engine = create_engine(url)
    with Session(engine) as session:
        found = [
            (e.actor.code if e.actor else None, e.result.value)
            for e in audit_history(session, action=action)
        ]
    engine.dispose()
    return found


def test_an_entry_goes_from_proposal_to_the_ledger(mcp_as: McpServers) -> None:
    agent = mcp_as("agent")
    entry = agent(
        "propose_journal_entry",
        **AWS,
        reason="Hosting",
        evidence=["vendor_id=42"],
    )
    assert (entry["status"], entry["total"]) == ("PROPOSED", "0.00")
    assert [line["amount"] for line in entry["lines"]] == ["120.50", "-120.50"]
    assert entry["lines"][0]["dimensions"] == {"DEPARTMENT": "ENG"}
    entry_id = entry["id"]

    validation = agent("validate_journal_entry", entry_id=entry_id)
    assert validation == {"journal_entry_id": entry_id, "is_valid": True, "issues": []}

    for tool, actor, status in [
        ("submit_for_approval", "agent", "PENDING_APPROVAL"),
        ("approve_journal_entry", "controller", "APPROVED"),
        ("post_journal_entry", "poster", "POSTED"),
    ]:
        assert mcp_as(actor)(tool, entry_id=entry_id)["status"] == status

    trial = mcp_as("reader")("get_trial_balance", as_of="2026-03-31")
    assert [(line["account_code"], line["balance"]) for line in trial["lines"]] == [
        ("2110", "-120.50"),
        ("6100", "120.50"),
    ]
    assert trial["total"] == "0.00"

    reversal = mcp_as("poster")(
        "reverse_journal_entry",
        entry_id=entry_id,
        entry_date="2026-03-31",
        reason="Duplicate",
    )
    assert (reversal["reversal_of"], reversal["status"]) == (entry_id, "POSTED")
    reversed_entry = mcp_as("reader")("get_journal_entry", entry_id=entry_id)
    assert (reversed_entry["status"], reversed_entry["reversed_by"]) == (
        "REVERSED",
        reversal["id"],
    )

    history = mcp_as("reader")(
        "get_audit_history", object_type="journal_entry", object_id=entry_id
    )["audit_events"]
    assert [(e["action"], e["actor"], e["actor_type"]) for e in history] == [
        ("propose_journal_entry", "agent", "AGENT"),
        ("validate_journal_entry", "agent", "AGENT"),
        ("submit_for_approval", "agent", "AGENT"),
        ("approve_journal_entry", "controller", "HUMAN"),
        ("post_journal_entry", "poster", "SYSTEM"),
        ("reverse_journal_entry", "poster", "SYSTEM"),
    ]
    assert (history[0]["reason"], history[0]["evidence"]) == (
        "Hosting",
        ["vendor_id=42"],
    )


def test_amounts_are_strings_and_a_float_never_gets_in(mcp_as: McpServers) -> None:
    lines = [{"account": "6100", "amount": 120.5}, AWS["lines"][1]]
    result = mcp_as("agent").call("propose_journal_entry", **{**AWS, "lines": lines})
    assert result.is_error
    assert result.structured_content is None  # refused before the tool ran
    assert "lines.0.amount" in result.content[0].text  # type: ignore[union-attr]
    assert _audited(mcp_as.url, "propose_journal_entry") == []


def test_unknown_arguments_are_refused_not_ignored(mcp_as: McpServers) -> None:
    agent = mcp_as("agent")
    invented = agent.call("propose_journal_entry", **AWS, post_immediately=True)
    assert invented.is_error and invented.structured_content is None

    old_form = {"account": "6100", "amount": "120.50", "debit": "120.50"}
    in_a_line = agent.call(
        "propose_journal_entry", **{**AWS, "lines": [old_form, AWS["lines"][1]]}
    )
    assert in_a_line.is_error and in_a_line.structured_content is None

    entry_id = _propose(agent)
    assert agent.call("submit_for_approval", entry_id=entry_id, approved=True).is_error
    status = mcp_as("reader")("get_journal_entry", entry_id=entry_id)["status"]
    assert status == "PROPOSED"
    assert _audited(mcp_as.url, "submit_for_approval") == []


def test_an_entry_the_kernel_cannot_record_is_refused_with_its_codes(
    mcp_as: McpServers,
) -> None:
    lines = [
        {"account": "9999", "amount": "10.00"},
        {"account": "2110", "amount": "-10.005"},
    ]
    refusal = mcp_as("agent").refusal(
        "propose_journal_entry", **{**AWS, "lines": lines}
    )
    assert refusal["error"] == "JournalEntryError"
    assert [issue["code"] for issue in refusal["issues"]] == [
        "UNKNOWN_ACCOUNT",
        "INVALID_AMOUNT",
    ]
    assert _audited(mcp_as.url, "propose_journal_entry") == [("agent", "REFUSED")]


def test_validation_reports_every_issue(mcp_as: McpServers) -> None:
    agent = mcp_as("agent")
    entry_id = _propose(
        agent,
        lines=[
            {"account": "6000", "amount": "120.50"},
            {"account": "2110", "amount": "-100.00"},
        ],
    )
    result = agent("validate_journal_entry", entry_id=entry_id)
    assert result["is_valid"] is False
    assert [(i["code"], i["line_number"]) for i in result["issues"]] == [
        ("UNBALANCED", None),
        ("ACCOUNT_NOT_POSTABLE", 1),
    ]

    refusal = agent.refusal("submit_for_approval", entry_id=entry_id)
    assert refusal["error"] == "JournalEntryError"
    assert [i["code"] for i in refusal["issues"]] == [
        "UNBALANCED",
        "ACCOUNT_NOT_POSTABLE",
    ]


@pytest.mark.parametrize(
    ("tool", "actor", "error", "detail"),
    [
        (
            "approve_journal_entry",
            "agent",
            "InvalidTransitionError",
            ("status", "PROPOSED"),
        ),
        (
            "post_journal_entry",
            "poster",
            "InvalidTransitionError",
            ("status", "PROPOSED"),
        ),
        (
            "validate_journal_entry",
            "reader",
            "PermissionDeniedError",
            ("permission", "PROPOSER"),
        ),
        (
            "reject_journal_entry",
            "controller",
            "InvalidTransitionError",
            ("action", "reject"),
        ),
    ],
)
def test_a_refusal_is_answered_as_the_audit_log_records_it(
    mcp_as: McpServers, tool: str, actor: str, error: str, detail: tuple[str, str]
) -> None:
    entry_id = _propose(mcp_as("agent"))
    refusal = mcp_as(actor).refusal(tool, entry_id=entry_id, reason="Checking")
    assert (refusal["error"], refusal[detail[0]]) == (error, detail[1])

    engine = create_engine(mcp_as.url)
    with Session(engine) as session:
        (event,) = audit_history(session, action=tool, result=AuditResult.REFUSED)
        assert event.output == refusal
    engine.dispose()


def test_approval_needs_an_approver_other_than_the_preparer(
    mcp_as: McpServers,
) -> None:
    engine = create_engine(mcp_as.url)
    with Session(engine) as session:
        lead = create_actor(
            session,
            code="lead",
            name="Lead",
            actor_type=ActorType.HUMAN,
            permissions=[
                Permission.READ_ONLY,
                Permission.PROPOSER,
                Permission.APPROVER,
            ],
        )
        key = issue_api_key(session, lead)
        session.commit()
    engine.dispose()
    lead_tools = mcp_as.with_key(key)

    entry_id = _propose(lead_tools)
    lead_tools("submit_for_approval", entry_id=entry_id)
    refusal = lead_tools.refusal("approve_journal_entry", entry_id=entry_id)
    assert refusal["error"] == "SegregationOfDutiesError"

    denied = mcp_as("agent").refusal("approve_journal_entry", entry_id=entry_id)
    assert (denied["error"], denied["permission"]) == (
        "PermissionDeniedError",
        "APPROVER",
    )
    approved = mcp_as("controller")("approve_journal_entry", entry_id=entry_id)
    assert approved["status"] == "APPROVED"
    assert _audited(mcp_as.url, "approve_journal_entry") == [
        ("lead", "REFUSED"),
        ("agent", "REFUSED"),
        ("controller", "SUCCEEDED"),
    ]


def test_posting_needs_approval_and_a_poster(mcp_as: McpServers) -> None:
    entry_id = _propose(mcp_as("agent"))
    mcp_as("agent")("submit_for_approval", entry_id=entry_id)
    early = mcp_as("poster").refusal("post_journal_entry", entry_id=entry_id)
    assert (early["error"], early["status"]) == (
        "InvalidTransitionError",
        "PENDING_APPROVAL",
    )

    mcp_as("controller")("approve_journal_entry", entry_id=entry_id)
    wrong = mcp_as("controller").refusal("post_journal_entry", entry_id=entry_id)
    assert wrong["permission"] == "POSTER"
    posted = mcp_as("poster")("post_journal_entry", entry_id=entry_id)
    assert (posted["status"], posted["posted_at"] is not None) == ("POSTED", True)
    ledger = mcp_as("reader")("get_ledger")["lines"]
    assert {line["entry_id"] for line in ledger} == {entry_id}


def test_rejecting_and_voiding_need_a_reason(mcp_as: McpServers) -> None:
    entry_id = _propose(mcp_as("agent"))
    mcp_as("agent")("submit_for_approval", entry_id=entry_id)
    controller = mcp_as("controller")

    missing = controller.call("reject_journal_entry", entry_id=entry_id)
    assert missing.is_error and missing.structured_content is None
    blank = controller.refusal("reject_journal_entry", entry_id=entry_id, reason=" ")
    assert (blank["error"], blank["message"]) == (
        "ValueError",
        "a reason is required to reject a journal entry",
    )
    rejected = controller(
        "reject_journal_entry", entry_id=entry_id, reason="Use account 5200"
    )
    assert rejected["status"] == "PROPOSED"

    voided = mcp_as("agent")("void_journal_entry", entry_id=entry_id, reason="Replaced")
    assert voided["status"] == "VOIDED"
    assert _audited(mcp_as.url, "reject_journal_entry") == [
        ("controller", "REFUSED"),
        ("controller", "SUCCEEDED"),
    ]


def test_an_unknown_entry_is_refused_before_the_workflow(mcp_as: McpServers) -> None:
    refusal = mcp_as("controller").refusal("approve_journal_entry", entry_id=999)
    assert refusal["error"] == "UnknownJournalEntryError"
    assert _audited(mcp_as.url, "approve_journal_entry") == []


def test_an_agent_takes_a_bill_through_the_workflow(mcp_as: McpServers) -> None:
    agent = mcp_as("agent")
    bill = agent(
        "observe_accounting_object",
        object_type="vendor_bill",
        occurred_at="2026-03-03T09:30:00Z",
        source="email",
        data={"raw": "PAPER TRAIL / INV-5 / 45.00"},
    )
    assert bill["status"] == "OBSERVED"
    bill_id = bill["id"]

    extracted = agent(
        "extract_accounting_object",
        object_id=bill_id,
        data={"invoice_number": "INV-5", "amount": "45.00"},
    )
    assert extracted["status"] == "EXTRACTED"

    lines = [
        {"account": "6700", "amount": "45.00"},
        {"account": "2110", "amount": "-45.00"},
    ]
    unsettled = agent.refusal(
        "propose_journal_entry",
        entry_date="2026-03-03",
        description="Paper Trail INV-5",
        lines=lines,
        accounting_object_id=bill_id,
    )
    assert unsettled["error"] == "CounterpartyRequiredError"

    wrong_kind = agent.refusal(
        "classify_accounting_object", object_id=bill_id, counterparty="C-HELIO"
    )
    assert wrong_kind["error"] == "CounterpartyKindError"
    classified = agent(
        "classify_accounting_object",
        object_id=bill_id,
        counterparty="V-PAPER",
        reason="The letterhead names Paper Trail",
        evidence=["invoice=INV-5"],
    )
    assert (classified["status"], classified["counterparty"]) == (
        "CLASSIFIED",
        "V-PAPER",
    )

    entry = agent(
        "propose_journal_entry",
        entry_date="2026-03-03",
        description="Paper Trail INV-5",
        lines=lines,
        accounting_object_id=bill_id,
    )
    assert entry["accounting_object_ids"] == [bill_id]
    recorded = mcp_as("reader")("get_accounting_object", object_id=bill_id)
    assert recorded["journal_entry_ids"] == [entry["id"]]

    in_use = mcp_as("controller").refusal(
        "void_accounting_object", object_id=bill_id, reason="Duplicate"
    )
    assert (in_use["error"], in_use["entry_ids"]) == (
        "ObjectHasAccountingImpactError",
        [entry["id"]],
    )


def test_business_data_refuses_floats_and_naive_times(mcp_as: McpServers) -> None:
    agent = mcp_as("agent")
    expense: dict[str, Any] = {
        "object_type": "expense",
        "occurred_at": "2026-03-03T09:30:00Z",
        "source": "card",
    }
    floated = agent.refusal(
        "observe_accounting_object", **expense, data={"amount": 12.5}
    )
    assert floated["error"] == "TypeError"
    naive = agent.refusal(
        "observe_accounting_object", **{**expense, "occurred_at": "2026-03-03T09:30:00"}
    )
    assert naive["error"] == "ValueError"
    assert _audited(mcp_as.url, "observe_accounting_object") == [
        ("agent", "REFUSED"),
        ("agent", "REFUSED"),
    ]


def test_periods_close_in_order_by_an_admin(mcp_as: McpServers) -> None:
    admin = mcp_as("admin")
    denied = mcp_as("controller").refusal("close_period", code="2026-01")
    assert denied["permission"] == "ADMIN"

    early = admin.refusal("close_period", code="2026-02")
    assert (early["error"], early["period_codes"]) == (
        "PeriodSequenceError",
        ["2026-01"],
    )
    assert admin("close_period", code="2026-01")["status"] == "CLOSED"

    blank = admin.refusal("reopen_period", code="2026-01", reason="")
    assert blank["error"] == "ValueError"
    reopened = admin("reopen_period", code="2026-01", reason="Late invoice")
    assert reopened["status"] == "OPEN"

    unknown = admin.refusal("close_period", code="1999-01")
    assert unknown["error"] == "UnknownPeriodError"

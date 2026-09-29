"""The MCP interface's read-only tools: how they are kept apart from the mutating
ones, who may call them, and what they return."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.orm import Session

from opensumma import workflow
from opensumma.db import create_engine
from opensumma.kernel import LineInput, create_journal_entry, post_journal_entry
from opensumma.mcp import mutating_tools, read_tools
from opensumma.objects import create_accounting_object
from opensumma.workflow import audit_history, deactivate_actor, get_actor

if TYPE_CHECKING:  # conftest is not importable while the tests run
    from conftest import McpServers

READ_TOOLS = {
    "get_chart_of_accounts",
    "get_account",
    "get_account_balance",
    "get_accounting_periods",
    "get_dimensions",
    "get_counterparties",
    "get_journal_entry",
    "get_ledger",
    "get_trial_balance",
    "get_income_statement",
    "get_balance_sheet",
    "get_general_ledger",
    "get_accounting_object",
    "search_accounting_objects",
    "get_audit_history",
}
MUTATING_TOOLS = {
    "propose_journal_entry",
    "validate_journal_entry",
    "submit_for_approval",
    "approve_journal_entry",
    "reject_journal_entry",
    "post_journal_entry",
    "void_journal_entry",
    "reverse_journal_entry",
    "observe_accounting_object",
    "extract_accounting_object",
    "classify_accounting_object",
    "void_accounting_object",
    "close_period",
    "reopen_period",
}

# Each read tool with arguments that name records the template books hold.
READS: list[tuple[str, dict[str, Any]]] = [
    ("get_chart_of_accounts", {}),
    ("get_account", {"code": "6100"}),
    ("get_account_balance", {"code": "6100"}),
    ("get_accounting_periods", {}),
    ("get_dimensions", {}),
    ("get_counterparties", {"kind": "VENDOR"}),
    ("get_ledger", {}),
    ("get_trial_balance", {"as_of": "2026-03-31"}),
    ("get_income_statement", {"start": "2026-03-01", "end": "2026-03-31"}),
    ("get_balance_sheet", {"as_of": "2026-03-31"}),
    ("get_general_ledger", {"start": "2026-03-01", "end": "2026-03-31"}),
    ("search_accounting_objects", {}),
    ("get_audit_history", {"limit": 1}),
]


def _post(url: str, *entries: tuple[date, list[LineInput]]) -> list[int]:
    """Post entries as trusted code, straight through the kernel."""
    engine = create_engine(url)
    with Session(engine) as session:
        ids = []
        for entry_date, lines in entries:
            entry = create_journal_entry(
                session, entry_date=entry_date, description="Posted", lines=lines
            )
            post_journal_entry(session, entry)
            ids.append(entry.id)
        session.commit()
    engine.dispose()
    return ids


def _audit_log_length(url: str) -> int:
    engine = create_engine(url)
    with Session(engine) as session:
        length = len(audit_history(session))
    engine.dispose()
    return length


def test_read_only_and_mutating_tools_are_kept_apart(mcp_as: McpServers) -> None:
    tools = {tool.name: tool for tool in mcp_as("reader").list_tools()}
    assert set(tools) == READ_TOOLS | MUTATING_TOOLS
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is (name in READ_TOOLS), name
        assert tool.annotations.open_world_hint is False, name

    assert {fn.__name__ for fn in read_tools.TOOLS} == READ_TOOLS
    assert {fn.__name__ for fn in mutating_tools.TOOLS} == MUTATING_TOOLS


def test_every_mutating_tool_is_the_workflow_operation_of_its_name() -> None:
    for name in MUTATING_TOOLS:
        assert callable(getattr(workflow, name)), name


def test_every_tool_describes_itself_and_refuses_unknown_arguments(
    mcp_as: McpServers,
) -> None:
    for tool in mcp_as("reader").list_tools():
        assert tool.description, tool.name
        assert tool.input_schema["additionalProperties"] is False, tool.name
        assert tool.output_schema is not None, tool.name

    result = mcp_as("reader").call("get_account", code="6100", as_of="2026-03-31")
    assert result.is_error
    assert result.structured_content is None
    assert "as_of" in result.content[0].text  # type: ignore[union-attr]


def test_every_read_needs_read_only_and_none_is_audited(mcp_as: McpServers) -> None:
    before = _audit_log_length(mcp_as.url)
    for tool, arguments in READS:
        assert not mcp_as("reader").call(tool, **arguments).is_error, tool
        assert mcp_as("nobody").refusal(tool, **arguments) == {
            "error": "PermissionDeniedError",
            "message": "actor nobody (AGENT) lacks the READ_ONLY permission",
            "permission": "READ_ONLY",
        }, tool
    assert _audit_log_length(mcp_as.url) == before


def test_a_server_acts_only_with_a_valid_key(mcp_as: McpServers) -> None:
    stranger = mcp_as.with_key("osk_not-a-key")
    assert stranger.refusal("get_chart_of_accounts") == {
        "error": "AuthenticationError",
        "message": "the API key is unknown or revoked",
    }


def test_revoking_the_key_cuts_off_a_running_server(mcp_as: McpServers) -> None:
    reader = mcp_as("reader")
    assert reader("get_account", code="6100")["code"] == "6100"

    engine = create_engine(mcp_as.url)
    with Session(engine) as session:
        workflow.revoke_api_keys(session, get_actor(session, "reader"))
        session.commit()
    engine.dispose()

    refusal = reader.refusal("get_account", code="6100")
    assert refusal["error"] == "AuthenticationError"


def test_an_inactive_actor_is_refused(mcp_as: McpServers) -> None:
    engine = create_engine(mcp_as.url)
    with Session(engine) as session:
        deactivate_actor(get_actor(session, "reader"))
        session.commit()
    engine.dispose()

    refusal = mcp_as("reader").refusal("get_chart_of_accounts")
    assert (refusal["error"], refusal["message"]) == (
        "PermissionDeniedError",
        "actor reader is inactive",
    )


def test_a_result_is_one_json_document_even_when_empty(mcp_as: McpServers) -> None:
    for tool, arguments in [
        ("get_accounting_periods", {}),
        ("search_accounting_objects", {}),
        ("get_account", {"code": "6100"}),
    ]:
        result = mcp_as("reader").call(tool, **arguments)
        assert len(result.content) == 1, tool
        text = result.content[0].text  # type: ignore[union-attr]
        assert json.loads(text) == result.structured_content, tool

    empty = mcp_as("reader")("search_accounting_objects")
    assert empty == {"accounting_objects": []}


def test_the_master_data_an_agent_needs_to_propose(mcp_as: McpServers) -> None:
    reader = mcp_as("reader")
    accounts = reader("get_chart_of_accounts")["accounts"]
    assert len(accounts) == 41
    assert reader("get_account", code="6000")["is_postable"] is False

    periods = reader("get_accounting_periods")["periods"]
    assert [p["code"] for p in periods][:2] == ["2026-01", "2026-02"]
    assert {p["status"] for p in periods} == {"OPEN"}

    dimensions = reader("get_dimensions")["dimensions"]
    assert "DEPARTMENT" in {d["code"] for d in dimensions}

    vendors = reader("get_counterparties", kind="VENDOR")["counterparties"]
    assert {c["kind"] for c in vendors} == {"VENDOR"}
    assert "V-PAPER" in {c["code"] for c in vendors}


@pytest.mark.parametrize(
    ("tool", "arguments", "error"),
    [
        ("get_account", {"code": "9999"}, "UnknownAccountError"),
        ("get_account_balance", {"code": "9999"}, "UnknownAccountError"),
        ("get_journal_entry", {"entry_id": 999}, "UnknownJournalEntryError"),
        ("get_accounting_object", {"object_id": 999}, "UnknownAccountingObjectError"),
        ("get_audit_history", {"actor": "ghost"}, "UnknownActorError"),
        ("get_counterparties", {"kind": "EMPLOYEE"}, None),
    ],
)
def test_an_unknown_record_is_refused_by_name(
    mcp_as: McpServers, tool: str, arguments: dict[str, Any], error: str | None
) -> None:
    result = mcp_as("reader").call(tool, **arguments)
    assert result.is_error
    if error is None:  # not a value the schema allows: refused before the tool
        assert result.structured_content is None
    else:
        assert result.structured_content is not None
        assert result.structured_content["error"] == error


def test_reports_and_the_ledger_derive_from_what_was_posted(
    mcp_as: McpServers,
) -> None:
    capital, aws = _post(
        mcp_as.url,
        (
            date(2026, 3, 1),
            [
                LineInput("1111", debit=Decimal("10000.00")),
                LineInput("3100", credit=Decimal("10000.00")),
            ],
        ),
        (
            date(2026, 3, 15),
            [
                LineInput("6100", debit=Decimal("120.50")),
                LineInput("2110", credit=Decimal("120.50")),
            ],
        ),
    )
    reader = mcp_as("reader")

    balance = reader("get_account_balance", code="6100", as_of="2026-03-31")
    assert (balance["debits"], balance["balance"]) == ("120.50", "120.50")
    before = reader("get_account_balance", code="6100", as_of="2026-03-14")
    assert before["balance"] == "0.00"

    lines = reader("get_ledger", accounts=["6100", "2110"])["lines"]
    assert [(line["entry_id"], line["account_code"]) for line in lines] == [
        (aws, "6100"),
        (aws, "2110"),
    ]
    march_first = reader("get_ledger", start="2026-03-01", end="2026-03-01")
    assert {line["entry_id"] for line in march_first["lines"]} == {capital}

    trial = reader("get_trial_balance", as_of="2026-03-31")
    assert (trial["total_debits"], trial["total_credits"], trial["is_balanced"]) == (
        "10120.50",
        "10120.50",
        True,
    )
    income = reader("get_income_statement", start="2026-03-01", end="2026-03-31")
    assert income["net_income"] == "-120.50"
    sheet = reader("get_balance_sheet", as_of="2026-03-31")
    assert (sheet["assets"]["total"], sheet["is_balanced"]) == ("10000.00", True)

    general = reader(
        "get_general_ledger", start="2026-03-01", end="2026-03-31", account="6100"
    )
    (account,) = general["accounts"]
    assert (account["opening_balance"], account["closing_balance"]) == (
        "0.00",
        "120.50",
    )

    entry = reader("get_journal_entry", entry_id=aws)
    assert (entry["status"], entry["total_debits"]) == ("POSTED", "120.50")


def test_accounting_objects_are_found_by_their_business_data(
    mcp_as: McpServers,
) -> None:
    engine = create_engine(mcp_as.url)
    with Session(engine) as session:
        for source in ("email", "vendor_portal"):
            create_accounting_object(
                session,
                object_type="vendor_bill",
                occurred_at=datetime(2026, 3, 20, 9, 30, tzinfo=UTC),
                source=source,
                counterparty="V-STRATUS",
                data={"invoice_number": "INV-2002", "amount": "80.00"},
            )
        session.commit()
    engine.dispose()
    reader = mcp_as("reader")

    found = reader(
        "search_accounting_objects",
        counterparty="V-STRATUS",
        data={"invoice_number": "INV-2002"},
    )["accounting_objects"]
    assert [obj["source"] for obj in found] == ["email", "vendor_portal"]
    assert reader("search_accounting_objects", data={"invoice_number": "INV-9"}) == {
        "accounting_objects": []
    }
    assert (
        len(reader("search_accounting_objects", source="email")["accounting_objects"])
        == 1
    )

    one = reader("get_accounting_object", object_id=found[0]["id"])
    assert (one["object_type"], one["data"]["amount"]) == ("vendor_bill", "80.00")

    # Data filters match text: a number is refused before the search runs.
    numeric = reader.call("search_accounting_objects", data={"invoice_number": 2002})
    assert numeric.is_error and numeric.structured_content is None

    naive = reader.refusal(
        "search_accounting_objects", occurred_from="2026-03-01T00:00:00"
    )
    assert naive["error"] == "ValueError"


def test_the_audit_history_is_read_page_by_page(mcp_as: McpServers) -> None:
    reader = mcp_as("reader")
    first = reader("get_audit_history", limit=5)["audit_events"]
    assert [event["sequence"] for event in first] == [1, 2, 3, 4, 5]
    after = first[-1]["sequence"]
    second = reader("get_audit_history", after=after, limit=5)["audit_events"]
    assert [event["sequence"] for event in second] == [6, 7, 8, 9, 10]

    too_many = reader.call("get_audit_history", limit=1001)
    assert too_many.is_error and too_many.structured_content is None

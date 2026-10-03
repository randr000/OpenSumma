"""Phase 8 acceptance: the MCP interface.

The server is started for real, as ``python -m opensumma.mcp``, and spoken to over
stdio, as an MCP host would launch it. Then an AI agent, a human controller, and a
posting service keep March's books through MCP tools alone, each through a server
acting as them: the agent reads the chart, proposes an entry and validates it, is
refused when it tries to approve its own work, the controller approves it, and the
service posts it. The entry is Dr 6100 120.50, Cr 2110 120.50; after it posts, the
trial balance at Mar 31 totals 120.50 on each side and net income is -120.50.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anyio.from_thread import start_blocking_portal
from mcp import Client, StdioServerParameters
from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import seed_chart_of_accounts
from opensumma.workflow import ActorType, audit_history, create_actor, issue_api_key

if TYPE_CHECKING:  # conftest is not importable while the tests run
    from conftest import McpServers

PROPOSAL: dict[str, Any] = {
    "entry_date": "2026-03-15",
    "description": "AWS, March",
    "lines": [
        {"account": "6100", "amount": "120.50"},
        {"account": "2110", "amount": "-120.50"},
    ],
    "reason": "Historical AWS transactions were classified to account 6100.",
    "evidence": ["vendor_id=42", "historical_account=6100"],
}


def test_the_mcp_server_starts(database_url: str) -> None:
    url = database_url
    init_db(url)
    engine = create_engine(url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        reader = create_actor(
            session,
            code="reader",
            name="Reader",
            actor_type=ActorType.AGENT,
            permissions=["READ_ONLY"],
        )
        key = issue_api_key(session, reader)
        session.commit()
    engine.dispose()

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opensumma.mcp", "--database-url", url],
        env={"OPENSUMMA_API_KEY": key},
    )
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(Client(server)) as client,
    ):
        assert client.server_info is not None
        assert client.server_info.name == "opensumma"
        assert client.instructions and "decimal strings" in client.instructions

        tools = portal.call(client.list_tools).tools
        assert len(tools) == 29
        result = portal.call(client.call_tool, "get_chart_of_accounts", {})
        assert not result.is_error
        assert result.structured_content is not None
        assert len(result.structured_content["accounts"]) == 41


def test_the_mcp_server_will_not_start_without_a_key(tmp_path: Path) -> None:
    environment = {k: v for k, v in os.environ.items() if k != "OPENSUMMA_API_KEY"}
    finished = subprocess.run(
        [sys.executable, "-m", "opensumma.mcp"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert finished.returncode == 2
    assert "OPENSUMMA_API_KEY" in finished.stderr


def test_read_tools_work(mcp_as: McpServers) -> None:
    reader = mcp_as("reader")
    checks: list[tuple[str, dict[str, Any], Any]] = [
        ("get_chart_of_accounts", {}, lambda r: len(r["accounts"]) == 41),
        ("get_account", {"code": "6100"}, lambda r: r["is_postable"]),
        ("get_account_balance", {"code": "6100"}, lambda r: r["balance"] == "0.00"),
        ("get_accounting_periods", {}, lambda r: len(r["periods"]) == 12),
        ("get_dimensions", {}, lambda r: len(r["dimensions"]) == 3),
        ("get_counterparties", {}, lambda r: len(r["counterparties"]) == 11),
        ("get_ledger", {}, lambda r: r["lines"] == []),
        ("get_trial_balance", {"as_of": "2026-03-31"}, lambda r: r["is_balanced"]),
        ("search_accounting_objects", {}, lambda r: r["accounting_objects"] == []),
        ("get_audit_history", {}, lambda r: len(r["audit_events"]) == 100),
    ]
    for tool, arguments, check in checks:
        assert check(reader(tool, **arguments)), tool


def test_proposal_tools_work(mcp_as: McpServers) -> None:
    entry = mcp_as("agent")("propose_journal_entry", **PROPOSAL)
    assert entry["status"] == "PROPOSED"
    assert [(line["account"], line["amount"]) for line in entry["lines"]] == [
        ("6100", "120.50"),
        ("2110", "-120.50"),
    ]
    assert entry["total"] == "0.00"
    assert mcp_as("reader")("get_journal_entry", entry_id=entry["id"]) == entry

    (audited,) = mcp_as("reader")(
        "get_audit_history", object_type="journal_entry", object_id=entry["id"]
    )["audit_events"]
    assert (audited["actor"], audited["actor_type"]) == ("agent", "AGENT")
    assert audited["reason"] == PROPOSAL["reason"]
    assert audited["evidence"] == PROPOSAL["evidence"]


def test_validation_tool_works(mcp_as: McpServers) -> None:
    agent = mcp_as("agent")
    good = agent("propose_journal_entry", **PROPOSAL)
    bad = agent(
        "propose_journal_entry",
        **{
            **PROPOSAL,
            "lines": [
                {"account": "6100", "amount": "120.50"},
                {"account": "2110", "amount": "-12.05"},
            ],
        },
    )

    assert agent("validate_journal_entry", entry_id=good["id"]) == {
        "journal_entry_id": good["id"],
        "is_valid": True,
        "issues": [],
    }
    result = agent("validate_journal_entry", entry_id=bad["id"])
    assert result["is_valid"] is False
    assert [issue["code"] for issue in result["issues"]] == ["UNBALANCED"]


def test_permission_checks_work(mcp_as: McpServers) -> None:
    agent, controller, poster = mcp_as("agent"), mcp_as("controller"), mcp_as("poster")
    entry_id = agent("propose_journal_entry", **PROPOSAL)["id"]
    agent("submit_for_approval", entry_id=entry_id)

    # A normal agent reads and proposes; it cannot approve or post its own work.
    own = agent.refusal("approve_journal_entry", entry_id=entry_id)
    assert (own["error"], own["permission"]) == ("PermissionDeniedError", "APPROVER")
    unapproved = poster.refusal("post_journal_entry", entry_id=entry_id)
    assert unapproved["error"] == "InvalidTransitionError"
    assert mcp_as("nobody").refusal("get_chart_of_accounts")["permission"] == (
        "READ_ONLY"
    )

    approved = controller(
        "approve_journal_entry", entry_id=entry_id, reason="Matches the invoice"
    )
    assert approved["status"] == "APPROVED"
    assert agent.refusal("post_journal_entry", entry_id=entry_id)["permission"] == (
        "POSTER"
    )
    assert poster("post_journal_entry", entry_id=entry_id)["status"] == "POSTED"

    reader = mcp_as("reader")
    trial = reader("get_trial_balance", as_of="2026-03-31")
    assert [(line["account_code"], line["balance"]) for line in trial["lines"]] == [
        ("2110", "-120.50"),
        ("6100", "120.50"),
    ]
    assert (trial["total"], trial["is_balanced"]) == ("0.00", True)
    income = reader("get_income_statement", start="2026-03-01", end="2026-03-31")
    assert income["net_income"] == "-120.50"

    engine = create_engine(mcp_as.url)
    with Session(engine) as session:
        attempts = [
            (event.action, event.actor.code if event.actor else None, event.result)
            for event in audit_history(session, object_type="journal_entry")
        ]
    engine.dispose()
    assert attempts == [
        ("propose_journal_entry", "agent", "SUCCEEDED"),
        ("submit_for_approval", "agent", "SUCCEEDED"),
        ("approve_journal_entry", "agent", "REFUSED"),
        ("post_journal_entry", "poster", "REFUSED"),
        ("approve_journal_entry", "controller", "SUCCEEDED"),
        ("post_journal_entry", "agent", "REFUSED"),
        ("post_journal_entry", "poster", "SUCCEEDED"),
    ]

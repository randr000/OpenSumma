"""The example agents' rules, and how they use the tools, without a database: the
tools are a script that answers each call as the MCP interface would."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest

from opensumma.agents import (
    DuplicateInvoiceAgent,
    ExampleAgents,
    InvestigationAgent,
    JournalEntryAgent,
    ToolRefusedError,
)
from opensumma.agents.common import Finding, majority, report
from opensumma.agents.duplicates import document_key, invoice_key
from opensumma.agents.investigation import (
    Deposit,
    Receipt,
    far_above,
    typical,
    unmatched_deposits,
)
from opensumma.agents.journal_entries import Shape, precedent, shape
from opensumma.benchmark import TASKS, TaskPrompt, ToolCall, Tools, load_agent


def _amounts(*values: str) -> list[Decimal]:
    return [Decimal(value) for value in values]


# --- Unusual amounts ----------------------------------------------------------------


def test_the_typical_amount_is_the_median() -> None:
    assert typical(_amounts("5", "1", "3")) == Decimal("3")
    # Of an even number, the geometric mean of the middle two.
    assert typical(_amounts("1", "4", "9", "100")) == Decimal("6.00")
    # So one huge amount beside one normal amount does not pass for typical.
    assert typical(_amounts("61.90", "10000.00")) < Decimal("1000")
    with pytest.raises(ValueError):
        typical([])


def test_an_amount_far_above_the_rest_is_unusual() -> None:
    usual = _amounts("12.00", "18.40", "25.00", "31.75", "44.10", "80.00")
    assert far_above(usual) == set()
    assert far_above([*usual, Decimal("10000.00")]) == {Decimal("10000.00")}


def test_unusual_amounts_do_not_hide_one_another() -> None:
    # Compared with the largest other amount, each of these would look ordinary.
    huge = _amounts("22000.00", "23200.00", "27800.00")
    usual = _amounts("140.00", "210.00", "300.00", "420.00", "645.65")
    assert far_above(usual + huge) == set(huge)


def test_a_gap_alone_is_not_enough() -> None:
    # Two meals, $18 and $260, are an order of magnitude apart, but neither is far
    # above what is typical of the two.
    assert far_above(_amounts("18.00", "260.00")) == set()
    assert far_above(_amounts("61.90", "10000.00")) == {Decimal("10000.00")}
    # Above a gap, the larger group is the usual one, not the unusual.
    assert far_above(_amounts("5", "900", "910", "950")) == set()
    assert far_above(_amounts("0.00", "0.00", "50.00")) == set()


# --- Agreement, and matching deposits -------------------------------------------------


def test_a_majority_is_more_than_half() -> None:
    assert majority(["6500", "6500", "6700"]) == ("6500", 2)
    assert majority(["6500", "6700"]) is None
    assert majority([]) is None


def _deposit(object_id: int, day: int, amount: str) -> Deposit:
    return Deposit(object_id, date(2026, 3, day), Decimal(amount))


def _receipt(entry_id: int, day: int, amount: str) -> Receipt:
    return Receipt(entry_id, date(2026, 3, day), Decimal(amount))


def test_a_deposit_is_matched_by_a_receipt_of_its_amount_in_the_week_before() -> None:
    deposits = [
        _deposit(1, 10, "500.00"),  # recorded the day before
        _deposit(2, 10, "700.00"),  # recorded the same day
        _deposit(3, 20, "900.00"),  # recorded after the bank received it
        _deposit(4, 20, "300.00"),  # recorded more than a week before
        _deposit(5, 20, "400.00"),  # a receipt of another amount
    ]
    receipts = [
        _receipt(11, 9, "500.00"),
        _receipt(12, 10, "700.00"),
        _receipt(13, 21, "900.00"),
        _receipt(14, 12, "300.00"),
        _receipt(15, 20, "400.01"),
    ]
    assert [d.object_id for d in unmatched_deposits(deposits, receipts)] == [3, 4, 5]


def test_a_receipt_matches_one_deposit_only() -> None:
    deposits = [_deposit(1, 10, "689.00"), _deposit(2, 12, "689.00")]
    receipts = [_receipt(11, 9, "689.00"), _receipt(12, 12, "689.00")]
    assert unmatched_deposits(deposits, receipts) == []
    # The first deposit takes the latest receipt before it, leaving the later one
    # for the second deposit.
    assert unmatched_deposits(deposits, receipts[:1]) == [deposits[1]]


# --- Duplicates ---------------------------------------------------------------------


def test_invoice_numbers_are_compared_as_letters_and_digits() -> None:
    assert invoice_key("STR-802918") == invoice_key("str 802918") == "str802918"
    assert invoice_key("STR-802918") != invoice_key("STR-802919")


def test_a_document_is_keyed_by_the_vendor_it_names() -> None:
    named = {
        "counterparty": None,
        "data": {"vendor_name": "Stratus", "invoice_number": "S-1"},
    }
    settled = {**named, "counterparty": "V-STRATUS"}
    assert document_key(named) == document_key(settled) == ("stratus", "s1")
    assert document_key({"counterparty": "V-X", "data": {}}) is None


# --- Findings -------------------------------------------------------------------------


def test_findings_are_reported_beside_the_answer() -> None:
    answer = report(
        "periods",
        [
            Finding("2026-11", "No accrual for November", ("period=2026-11",)),
            Finding("2026-03", "No accrual for March", ("period=2026-03",)),
        ],
    )
    assert answer["periods"] == ["2026-03", "2026-11"]
    assert answer["findings"][0] == {
        "item": "2026-03",
        "reason": "No accrual for March",
        "evidence": ["period=2026-03"],
    }


# --- Using the tools ------------------------------------------------------------------

Script = Callable[[str, dict[str, Any]], ToolCall]


class ScriptedTools:
    """Stands in for the tools: each call is answered by ``script``, and recorded."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: list[ToolCall] = []

    def call(self, tool: str, /, **arguments: Any) -> ToolCall:
        self.calls.append(self.script(tool, arguments))
        return self.calls[-1]


def _prompt(task_id: str, instructions: str = "") -> TaskPrompt:
    return TaskPrompt(task_id, "A task", instructions, {})


def _tools(script: Script) -> tuple[ScriptedTools, Tools]:
    scripted = ScriptedTools(script)
    return scripted, cast(Tools, scripted)


def test_each_task_has_one_example_agent() -> None:
    agents = (InvestigationAgent(), JournalEntryAgent(), DuplicateInvoiceAgent())
    for task in TASKS:
        assert sum(agent.handles(task.id) for agent in agents) == 1, task.id


def test_an_agent_declines_what_it_does_not_handle() -> None:
    def no_calls(tool: str, arguments: dict[str, Any]) -> ToolCall:
        raise AssertionError(f"called {tool}")

    scripted, tools = _tools(no_calls)
    assert DuplicateInvoiceAgent().run(_prompt("GL-001"), tools) == {}
    assert ExampleAgents().run(_prompt("XX-001"), tools) == {}
    assert scripted.calls == []


def test_a_refused_call_stops_the_agent_and_says_why() -> None:
    def refuse(tool: str, arguments: dict[str, Any]) -> ToolCall:
        error = {"error": "UnknownAccountError", "message": "no account 9999"}
        return ToolCall(1, tool, arguments, error=error)

    _, tools = _tools(refuse)
    task = _prompt(
        "GL-001", "What was the balance of account 9999 at the end of 2026-03-31?"
    )
    with pytest.raises(
        ToolRefusedError,
        match="get_account_balance was refused: UnknownAccountError: no account 9999",
    ):
        InvestigationAgent().run(task, tools)


def test_an_agent_says_what_the_instructions_do_not_tell_it() -> None:
    _, tools = _tools(lambda tool, arguments: ToolCall(1, tool, arguments, result={}))
    with pytest.raises(ValueError, match="do not say which entry"):
        JournalEntryAgent().run(_prompt("JE-002", "Validate the entry."), tools)


Line = tuple[str, str, dict[str, str]]  # account, signed amount, tags


def _entry(entry_id: int, status: str, *lines: Line) -> dict[str, Any]:
    return {
        "id": entry_id,
        "status": status,
        "lines": [
            {"account": account, "amount": amount, "dimensions": tags}
            for account, amount, tags in lines
        ],
    }


def test_an_entry_shape_is_its_accounts_and_the_tags_on_each_side() -> None:
    tags = {"LOCATION": "HQ", "DEPARTMENT": "GA"}
    bill = _entry(1, "POSTED", ("6700", "45.00", tags), ("2110", "-45.00", {}))
    assert shape(bill) == Shape("6700", ("DEPARTMENT", "LOCATION"), "2110", ())
    split = _entry(
        2,
        "POSTED",
        ("6700", "40.00", {}),
        ("6100", "5.00", {}),
        ("2110", "-45.00", {}),
    )
    assert shape(split) is None


def test_a_zero_line_does_not_change_how_an_entry_records_its_bill() -> None:
    bill = _entry(
        1,
        "POSTED",
        ("6700", "45.00", {}),
        ("6100", "0.00", {}),
        ("2110", "-45.00", {}),
    )
    assert shape(bill) == Shape("6700", (), "2110", ())


def _history(*entries: dict[str, Any]) -> Script:
    """The tools of books where vendor V-PAPER sent the bills ``entries`` record,
    most recent first, and bill 99, which has just arrived."""
    bills = [
        {"id": 99, "occurred_at": "2026-12-30T09:30:00+00:00", "journal_entry_ids": []},
        *(
            {
                "id": entry["id"] + 100,
                "occurred_at": f"2026-{12 - n:02d}-01T09:00:00+00:00",
                "journal_entry_ids": [entry["id"]],
            }
            for n, entry in enumerate(entries, start=1)
        ),
    ]
    by_id = {entry["id"]: entry for entry in entries}

    def answer(tool: str, arguments: dict[str, Any]) -> ToolCall:
        if tool == "search_accounting_objects":
            assert arguments == {
                "object_type": "vendor_bill",
                "counterparty": "V-PAPER",
            }
            return ToolCall(1, tool, arguments, result={"accounting_objects": bills})
        assert tool == "get_journal_entry"
        return ToolCall(1, tool, arguments, result=by_id[arguments["entry_id"]])

    return answer


BILL = {"id": 99, "object_type": "vendor_bill", "counterparty": "V-PAPER"}


def test_a_bill_is_recorded_as_most_of_the_vendors_latest_posted_bills_were() -> None:
    usual: tuple[Line, Line] = (
        ("6700", "10.00", {"DEPARTMENT": "GA"}),
        ("2110", "-10.00", {}),
    )
    wrong: tuple[Line, Line] = (
        ("6500", "10.00", {"DEPARTMENT": "GA"}),
        ("2110", "-10.00", {}),
    )
    _, tools = _tools(
        _history(
            _entry(1, "POSTED", *usual),
            _entry(2, "POSTED", *wrong),
            _entry(3, "VOIDED", *wrong),  # never posted, so no precedent
            _entry(4, "POSTED", *usual),
        )
    )
    learned = precedent(tools, BILL)
    assert learned.shape == Shape("6700", ("DEPARTMENT",), "2110", ())
    assert (learned.entries, learned.considered) == ((1, 4), 3)


def test_without_a_posted_bill_to_learn_from_the_agent_does_not_guess() -> None:
    _, tools = _tools(_history())
    with pytest.raises(ValueError, match="no posted bill from V-PAPER"):
        precedent(tools, BILL)


# --- Loading ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("examples", ExampleAgents),
        ("investigator", InvestigationAgent),
        ("journal-entry", JournalEntryAgent),
        ("duplicate-invoice", DuplicateInvoiceAgent),
    ],
)
def test_the_example_agents_are_loaded_by_name(name: str, kind: type[Any]) -> None:
    agent = load_agent(name)
    assert isinstance(agent, kind)
    assert agent.name == name

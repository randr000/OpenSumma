"""What the example agents share: calling the tools, reading the books through
them, and reporting what they found with the reasons and evidence for it.

An agent sees the books only as the tools present them: JSON documents, with
amounts as decimal strings and dates as ISO strings. ``Books`` reads them once per
task and indexes them, so an agent's checks share its reads.
"""

import re
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from opensumma.benchmark import TaskPrompt, ToolCall, Tools

CENT = Decimal("0.01")

Workflow = Callable[[TaskPrompt, Tools], dict[str, Any]]


class ToolRefusedError(Exception):
    """A tool refused a call the agent cannot do without."""

    def __init__(self, call: ToolCall) -> None:
        error = call.error or {}
        super().__init__(
            f"{call.tool} was refused: {error.get('error')}: {error.get('message')}"
        )
        self.call = call


def use(tools: Tools, tool: str, /, **arguments: Any) -> Any:
    """The result of calling ``tool``. A refusal stops the agent, saying why."""
    call = tools.call(tool, **arguments)
    if not call.ok:
        raise ToolRefusedError(call)
    return call.result


def find(pattern: str, task: TaskPrompt, what: str) -> str:
    """The first group of ``pattern`` in the task's instructions."""
    found = re.search(pattern, task.instructions)
    if found is None:
        raise ValueError(f"the instructions do not say {what}")
    return found.group(1)


def majority[T: Hashable](values: Iterable[T]) -> tuple[T, int] | None:
    """The value more than half of ``values`` share, and how many do, if any does."""
    counted = Counter(values)
    if not counted:
        return None
    value, count = counted.most_common(1)[0]
    return (value, count) if 2 * count > counted.total() else None


class WorkflowAgent:
    """An agent with a workflow for some of the benchmark's tasks, which declines
    the others with an empty answer."""

    name: str
    workflows: ClassVar[Mapping[str, Workflow]]

    def handles(self, task_id: str) -> bool:
        return task_id in self.workflows

    def run(self, task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        workflow = self.workflows.get(task.id)
        return {} if workflow is None else workflow(task, tools)


# --- Findings ---------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """Something found in the books: the item the answer lists, and a concise
    reason and references to the evidence, reported beside the answer."""

    item: Any
    reason: str
    evidence: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


def report(field: str, findings: Sequence[Finding]) -> dict[str, Any]:
    """An answer listing the findings' items in ``field``, and each finding with its
    reason and evidence in ``findings``, a field the benchmark does not score."""
    ordered = sorted(findings, key=lambda finding: str(finding.item))
    return {
        field: [finding.item for finding in ordered],
        "findings": [finding.to_json() for finding in ordered],
    }


# --- The books, as the tools present them -------------------------------------------


@dataclass(frozen=True)
class Posted:
    """A posted journal entry, as the ledger holds it."""

    id: int
    entry_date: date
    lines: tuple[dict[str, Any], ...]

    @property
    def total(self) -> Decimal:
        return sum((Decimal(line["debit"]) for line in self.lines), Decimal("0.00"))

    @property
    def debited_accounts(self) -> set[str]:
        return {line["account_code"] for line in self.lines if Decimal(line["debit"])}


class Books:
    """The books as one agent reads them through the tools, each read made once."""

    def __init__(self, tools: Tools) -> None:
        self.tools = tools
        self._reads: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = {}
        self._ledger: dict[int, Posted] | None = None

    def _read(self, tool: str, **arguments: str) -> Any:
        key = (tool, tuple(sorted(arguments.items())))
        if key not in self._reads:
            self._reads[key] = use(self.tools, tool, **arguments)
        return self._reads[key]

    def objects(self, object_type: str | None = None) -> list[dict[str, Any]]:
        """The accounting objects of one type, or of every type, not voided."""
        arguments = {} if object_type is None else {"object_type": object_type}
        found = self._read("search_accounting_objects", **arguments)
        return [o for o in found["accounting_objects"] if o["status"] != "VOIDED"]

    def accounts(self) -> dict[str, dict[str, Any]]:
        found = self._read("get_chart_of_accounts")["accounts"]
        return {account["code"]: account for account in found}

    def periods(self) -> list[dict[str, Any]]:
        periods: list[dict[str, Any]] = self._read("get_accounting_periods")["periods"]
        return periods

    def counterparties(self, kind: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = self._read("get_counterparties", kind=kind)[
            "counterparties"
        ]
        return found

    def ledger(self) -> dict[int, Posted]:
        """Every posted entry, by id."""
        if self._ledger is None:
            lines: dict[int, list[dict[str, Any]]] = {}
            for line in use(self.tools, "get_ledger")["lines"]:
                lines.setdefault(line["entry_id"], []).append(line)
            self._ledger = {
                entry_id: Posted(
                    entry_id, date.fromisoformat(found[0]["entry_date"]), tuple(found)
                )
                for entry_id, found in lines.items()
            }
        return self._ledger

    def posted_entry(self, obj: Mapping[str, Any]) -> Posted | None:
        """The posted entry that records ``obj``, if exactly one does."""
        ledger = self.ledger()
        posted = [ledger[i] for i in obj["journal_entry_ids"] if i in ledger]
        return posted[0] if len(posted) == 1 else None

    def period_of(self, day: date) -> str | None:
        """The code of the accounting period ``day`` falls in, if any."""
        for period in self.periods():
            start = date.fromisoformat(period["start_date"])
            if start <= day <= date.fromisoformat(period["end_date"]):
                code: str = period["code"]
                return code
        return None

"""Running the benchmark: every task in its own workspace, scored and recorded.

For each task the runner copies the dataset's books into a workspace, prepares the
task there, serves the MCP tools to the agent as the actor ``agent``, and scores
its answer. The metrics come from three deterministic records: the answer, the
trajectory of tool calls, and the audit log the agent's actions left in the
workspace.

A run writes, into its output directory:

- ``results.json``: every task's score, answer, expected answer, and metrics, and
  the summary across tasks;
- ``results.csv``: one row per task, for spreadsheets;
- ``trajectories/<task>.jsonl``: every tool call, in order, and the answer;
- ``workspaces/<task>/books.db``: the books as the agent left them, with its audit
  log.

Nothing in the results depends on when the run happened, so a deterministic agent
gets the same results every time.
"""

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import Client
from pydantic import ValidationError
from sqlalchemy.orm import Session

from opensumma.benchmark.agents import Agent, OracleAgent
from opensumma.benchmark.environment import (
    DatasetFiles,
    Environment,
    create_workspace,
    load_dataset,
    open_environment,
)
from opensumma.benchmark.schema import (
    NUMERICAL,
    WORKFLOW,
    Answer,
    Instance,
    Score,
    Task,
)
from opensumma.benchmark.tasks import TASKS, get_task
from opensumma.benchmark.tools import ToolCall, Tools
from opensumma.kernel import JournalEntry, find_account, find_period
from opensumma.mcp import create_server
from opensumma.mcp.mutating_tools import TOOLS as MUTATING_TOOLS
from opensumma.objects import (
    AccountingObject,
    find_counterparty,
    search_accounting_objects,
)
from opensumma.workflow import AuditResult, audit_history

BENCHMARK_VERSION = 2
FORMAT = 1
RESULTS_FILE = "results.json"
RESULTS_CSV = "results.csv"
TRAJECTORIES = "trajectories"
WORKSPACES = "workspaces"

# Changes to the books an agent can attempt; validating changes nothing.
WORKFLOW_ACTIONS = frozenset(
    tool.__name__
    for tool in MUTATING_TOOLS
    if tool.__name__ != "validate_journal_entry"
)
# Answer fields that name a record of the books, and what kind of record.
_REFERENCES = {
    "journal_entry_id": "journal_entry",
    "journal_entry_ids": "journal_entry",
    "accounting_object_id": "accounting_object",
    "accounting_object_ids": "accounting_object",
    "account": "account",
    "correct_account": "account",
    "vendor": "counterparty",
    "invoice_numbers": "invoice",
    "periods": "period",
    "correct_period": "period",
}


@dataclass(frozen=True)
class Usage:
    """How the agent went about a task, counted from its trajectory, the audit log,
    and its answer."""

    tool_calls: int = 0
    rejected_calls: int = 0  # no such tool, or arguments its schema refused
    unknown_record_calls: int = 0  # refused for naming a record that does not exist
    workflow_actions: int = 0  # attempts to change the books
    refused_workflow_actions: int = 0
    changes: int = 0  # changes the agent made, from the audit log
    documented_changes: int = 0  # with a reason and at least one evidence reference
    references: int = 0  # records the answer names
    hallucinated_references: int = 0  # of those, the ones the books do not hold


@dataclass(frozen=True)
class TaskResult:
    task: Task
    instructions: str
    expected: Mapping[str, Any]
    answer: Mapping[str, Any] | None
    answer_error: str | None
    agent_error: str | None
    score: Score
    usage: Usage
    calls: tuple[ToolCall, ...]

    def to_json(self) -> dict[str, Any]:
        score = self.score
        return {
            "id": self.task.id,
            "title": self.task.title,
            "category": self.task.category,
            "measures": sorted(self.task.measures),
            "instructions": self.instructions,
            "score": _round(score.score),
            "numerical": _round(score.numerical),
            "classified": score.classified,
            "correctly_classified": score.correctly_classified,
            "workflow": _round(score.workflow),
            "details": dict(score.details),
            "answer": self.answer,
            "answer_error": self.answer_error,
            "agent_error": self.agent_error,
            "expected": dict(self.expected),
            "usage": vars(self.usage),
        }


@dataclass(frozen=True)
class BenchmarkResult:
    agent: str
    dataset: Mapping[str, Any]
    tasks: tuple[TaskResult, ...]

    def summary(self) -> dict[str, Any]:
        """The benchmark's metrics across every task.

        A metric no task measured, such as auditability when the agent changed
        nothing, is None rather than a score it did not earn.
        """
        results = self.tasks
        scores = [r.score for r in results]

        def total(name: str) -> int:
            return sum(getattr(r.usage, name) for r in results)

        def share(part: int, whole: int) -> float | None:
            return None if whole == 0 else part / whole

        def mean(values: Sequence[float | None]) -> float | None:
            known = [v for v in values if v is not None]
            return None if not known else sum(known) / len(known)

        calls = total("tool_calls")
        misused = total("rejected_calls") + total("unknown_record_calls")
        metrics = {
            "accounting_correctness": mean([s.score for s in scores]),
            "numerical_correctness": mean([s.numerical for s in scores]),
            "classification_accuracy": share(
                sum(s.correctly_classified for s in scores),
                sum(s.classified for s in scores),
            ),
            "workflow_accuracy": mean([s.workflow for s in scores]),
            "tool_use_accuracy": share(calls - misused, calls),
            "hallucination_rate": share(
                total("hallucinated_references"), total("references")
            ),
            "invalid_posting_rate": share(
                total("refused_workflow_actions"), total("workflow_actions")
            ),
            "auditability": share(total("documented_changes"), total("changes")),
        }
        return {
            "tasks": len(results),
            **{name: _round(value) for name, value in metrics.items()},
            "tool_calls": calls,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "benchmark_version": BENCHMARK_VERSION,
            "agent": self.agent,
            "dataset": dict(self.dataset),
            "summary": self.summary(),
            "tasks": [result.to_json() for result in self.tasks],
        }


def run_benchmark(
    dataset: DatasetFiles | Path | str,
    agent: Agent,
    output: Path | str,
    task_ids: Sequence[str] | None = None,
) -> BenchmarkResult:
    """Run ``agent`` on every task, or on ``task_ids``, and write the results to
    ``output``, which must not exist yet or be empty."""
    files = dataset if isinstance(dataset, DatasetFiles) else load_dataset(dataset)
    tasks = TASKS if task_ids is None else tuple(get_task(i) for i in task_ids)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"{output} is not empty; choose another directory")
    output.mkdir(parents=True, exist_ok=True)
    with start_blocking_portal() as portal:
        results = tuple(_run_task(task, files, agent, output, portal) for task in tasks)
    result = BenchmarkResult(agent.name, files.summary(), results)
    _export(result, output)
    return result


def _run_task(
    task: Task,
    dataset: DatasetFiles,
    agent: Agent,
    output: Path,
    portal: BlockingPortal,
) -> TaskResult:
    workspace = create_workspace(
        dataset, output / WORKSPACES / task.id, task.permissions
    )
    with open_environment(workspace, dataset, task.id) as env:
        instance = task.instance(env)
        env.session.commit()

    server = create_server(workspace.url, api_key=workspace.agent_key)
    with portal.wrap_async_context_manager(Client(server)) as client:
        tools = Tools(portal, client)
        answer, agent_error = _ask(agent, instance, tools)

    with open_environment(workspace, dataset, task.id) as env:
        parsed, answer_error = _parse(task, answer)
        score = (
            _unanswered(task) if parsed is None else task.score(instance, parsed, env)
        )
        usage = _usage(env, tools.calls, parsed)
    return TaskResult(
        task,
        instance.instructions,
        instance.expected,
        answer,
        answer_error,
        agent_error,
        score,
        usage,
        tuple(tools.calls),
    )


def _ask(
    agent: Agent, instance: Instance, tools: Tools
) -> tuple[dict[str, Any] | None, str | None]:
    """The agent's answer as JSON values, or what went wrong."""
    try:
        if isinstance(agent, OracleAgent):
            raw = agent.solve(instance, tools)
        else:
            raw = agent.run(instance.prompt, tools)
        answer: dict[str, Any] = json.loads(json.dumps(dict(raw)))
    except Exception as error:  # the agent's failure is part of its result
        return None, f"{type(error).__name__}: {error}"
    return answer, None


def _parse(
    task: Task, answer: Mapping[str, Any] | None
) -> tuple[Answer | None, str | None]:
    if answer is None:
        return None, None
    try:
        return task.answer.model_validate(answer), None
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'answer'}: {e['msg']}"
            for e in error.errors()
        )
        return None, f"the answer does not match the task's schema: {problems}"


def _unanswered(task: Task) -> Score:
    return Score(
        0.0,
        numerical=0.0 if NUMERICAL in task.measures else None,
        workflow=0.0 if WORKFLOW in task.measures else None,
    )


def _usage(env: Environment, calls: Sequence[ToolCall], answer: Answer | None) -> Usage:
    actions = [c for c in calls if c.tool in WORKFLOW_ACTIONS and not c.rejected]
    changes = [
        event
        for event in audit_history(
            env.session, actor=env.agent, result=AuditResult.SUCCEEDED
        )
        if event.action != "validate_journal_entry"
    ]
    references = [] if answer is None else _references(answer.model_dump())
    return Usage(
        tool_calls=len(calls),
        rejected_calls=sum(c.rejected for c in calls),
        unknown_record_calls=sum(c.names_unknown_record for c in calls),
        workflow_actions=len(actions),
        refused_workflow_actions=sum(c.refused for c in actions),
        changes=len(changes),
        documented_changes=sum(bool(e.reason and e.evidence) for e in changes),
        references=len(references),
        hallucinated_references=sum(
            not _exists(env.session, kind, value) for kind, value in references
        ),
    )


def _references(value: Any, key: str | None = None) -> list[tuple[str, Any]]:
    """Every record an answer names, as (kind, value)."""
    if isinstance(value, dict):
        return [ref for k, v in value.items() for ref in _references(v, k)]
    if isinstance(value, list):
        return [ref for item in value for ref in _references(item, key)]
    if key in _REFERENCES and value is not None:
        return [(_REFERENCES[key], value)]
    return []


def _exists(session: Session, kind: str, value: Any) -> bool:
    if kind == "journal_entry":
        return session.get(JournalEntry, value) is not None
    if kind == "accounting_object":
        return session.get(AccountingObject, value) is not None
    if kind == "account":
        return find_account(session, str(value)) is not None
    if kind == "counterparty":
        return find_counterparty(session, str(value)) is not None
    if kind == "period":
        return find_period(session, str(value)) is not None
    if kind == "invoice":
        return bool(search_accounting_objects(session, data={"invoice_number": value}))
    raise AssertionError(f"unknown kind of record {kind!r}")


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


_CSV_COLUMNS = (
    "id",
    "category",
    "score",
    "numerical",
    "classified",
    "correctly_classified",
    "workflow",
    *Usage.__dataclass_fields__,
    "answer_error",
    "agent_error",
)


def _export(result: BenchmarkResult, output: Path) -> None:
    document = result.to_json()
    (output / RESULTS_FILE).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    with (output / RESULTS_CSV).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for task in document["tasks"]:
            writer.writerow({**task, **task["usage"]})
    trajectories = output / TRAJECTORIES
    trajectories.mkdir()
    for task in result.tasks:
        lines = [json.dumps(call.to_json()) for call in task.calls]
        lines.append(
            json.dumps(
                {
                    "answer": task.answer,
                    "answer_error": task.answer_error,
                    "agent_error": task.agent_error,
                }
            )
        )
        (trajectories / f"{task.task.id}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

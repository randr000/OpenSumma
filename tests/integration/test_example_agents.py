"""The example agents, run by the benchmark: what they find, how they record
entries, and that everything they change is a tool call they made, audited."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from opensumma.agents import (
    DuplicateInvoiceAgent,
    ExampleAgents,
    InvestigationAgent,
    JournalEntryAgent,
)
from opensumma.benchmark import (
    BenchmarkResult,
    TaskResult,
    load_dataset,
    run_benchmark,
)
from opensumma.benchmark.environment import DatasetFiles
from opensumma.datasets import write_dataset
from opensumma.db import create_engine
from opensumma.kernel import JournalEntry, JournalEntryStatus
from opensumma.mcp.mutating_tools import TOOLS as MUTATING_TOOLS
from opensumma.workflow import audit_history, get_actor, workflow_history

FINDING_TASKS = (
    "GL-003",
    "GL-004",
    "GL-005",
    "GL-006",
    "AP-001",
    "AP-002",
    "AP-003",
    "AP-004",
    "AR-002",
    "CLOSE-001",
)
MUTATING = {tool.__name__ for tool in MUTATING_TOOLS}


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> DatasetFiles:
    directory = tmp_path_factory.mktemp("dataset") / "acme"
    write_dataset(directory, company="acme", transactions=400, seed=7, errors=22)
    return load_dataset(directory)


@pytest.fixture(scope="module")
def team(
    dataset: DatasetFiles, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, BenchmarkResult]:
    output = tmp_path_factory.mktemp("examples")
    return output, run_benchmark(dataset, ExampleAgents(), output)


def _task(result: BenchmarkResult, task_id: str) -> TaskResult:
    (found,) = [task for task in result.tasks if task.task.id == task_id]
    return found


@contextmanager
def _workspace(output: Path, task_id: str) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{output / 'workspaces' / task_id / 'books.db'}")
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def test_the_example_agents_solve_every_task(
    team: tuple[Path, BenchmarkResult],
) -> None:
    _, result = team
    for task in result.tasks:
        assert (task.score.score, task.agent_error, task.answer_error) == (
            1.0,
            None,
            None,
        ), task.task.id
    summary = result.summary()
    assert summary["accounting_correctness"] == 1.0
    assert summary["classification_accuracy"] == 1.0
    assert summary["workflow_accuracy"] == 1.0
    assert summary["tool_use_accuracy"] == 1.0
    assert summary["hallucination_rate"] == 0.0
    assert summary["invalid_posting_rate"] == 0.0
    assert summary["auditability"] == 1.0


def test_on_clean_books_they_find_nothing(tmp_path: Path) -> None:
    write_dataset(
        tmp_path / "clean", company="acme", transactions=400, seed=7, errors=0
    )
    result = run_benchmark(
        tmp_path / "clean", ExampleAgents(), tmp_path / "results", FINDING_TASKS
    )
    for task in result.tasks:
        assert task.answer is not None
        assert task.answer["findings"] == [], task.task.id
        assert task.score.score == 1.0, task.task.id


def test_every_finding_comes_with_its_reason_and_evidence(
    team: tuple[Path, BenchmarkResult],
) -> None:
    _, result = team
    for task_id in FINDING_TASKS:
        answer = _task(result, task_id).answer
        assert answer is not None
        (listed,) = [value for key, value in answer.items() if key != "findings"]
        assert listed and [f["item"] for f in answer["findings"]] == listed, task_id
        for finding in answer["findings"]:
            assert finding["reason"] and finding["evidence"], task_id


def test_an_agent_works_only_on_its_own_tasks(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    for agent, own, other in (
        (InvestigationAgent(), "GL-004", "AP-001"),
        (DuplicateInvoiceAgent(), "AP-001", "JE-002"),
        (JournalEntryAgent(), "JE-002", "GL-004"),
    ):
        result = run_benchmark(dataset, agent, tmp_path / agent.name, [own, other])
        worked, declined = result.tasks
        assert worked.score.score == 1.0 and worked.calls
        assert (declined.answer, declined.calls) == ({}, ())


def test_the_readers_change_nothing(team: tuple[Path, BenchmarkResult]) -> None:
    _, result = team
    agents = (InvestigationAgent(), DuplicateInvoiceAgent())
    for task in result.tasks:
        if any(agent.handles(task.task.id) for agent in agents):
            assert task.usage.workflow_actions == task.usage.changes == 0
            assert not any(call.tool in MUTATING for call in task.calls)


def test_the_journal_entry_agent_leaves_its_entry_for_a_person_to_approve(
    team: tuple[Path, BenchmarkResult],
) -> None:
    output, result = team
    answer = _task(result, "JE-001").answer
    assert answer is not None
    with _workspace(output, "JE-001") as session:
        entry = session.get(JournalEntry, answer["journal_entry_id"])
        assert entry is not None
        assert entry.status is JournalEntryStatus.PENDING_APPROVAL
        steps = [
            (s.action.value, s.actor.code) for s in workflow_history(session, entry)
        ]
        assert steps == [("propose", "agent"), ("submit", "agent")]
        events = audit_history(session, subject=entry)
        assert [(e.action, e.actor_type.value) for e in events] == [
            ("propose_journal_entry", "AGENT"),
            ("validate_journal_entry", "AGENT"),
            ("submit_for_approval", "AGENT"),
        ]
        proposed = events[0]
        assert proposed.reason is not None and "latest" in proposed.reason
        # The evidence names the entries it learned from, which are in the books.
        precedents = [
            int(reference.split("=")[1])
            for reference in proposed.evidence
            if reference.startswith("precedent_journal_entry_id=")
        ]
        assert precedents
        for entry_id in precedents:
            earlier = session.get(JournalEntry, entry_id)
            assert earlier is not None
            assert earlier.status is JournalEntryStatus.POSTED


def test_a_correction_voids_the_invalid_entry_and_says_why(
    team: tuple[Path, BenchmarkResult],
) -> None:
    output, result = team
    je_003 = _task(result, "JE-003")
    assert je_003.answer is not None
    voided_id = je_003.answer["voided_journal_entry_id"]
    with _workspace(output, "JE-003") as session:
        voided = session.get(JournalEntry, voided_id)
        assert voided is not None and voided.status is JournalEntryStatus.VOIDED
        (void,) = audit_history(session, subject=voided, action="void_journal_entry")
        assert void.actor is not None and void.actor.code == "agent"
        issues = [e.split("=")[1] for e in void.evidence if e.startswith("issue=")]
        assert issues and all(code in (void.reason or "") for code in issues)


def test_everything_the_agents_change_is_a_tool_call_they_made(
    team: tuple[Path, BenchmarkResult],
) -> None:
    output, result = team
    for task in result.tasks:
        with _workspace(output, task.task.id) as session:
            events = audit_history(session, actor=get_actor(session, "agent"))
            audited = [(e.action, e.result.value) for e in events]
        called = [
            (call.tool, "SUCCEEDED" if call.ok else "REFUSED")
            for call in task.calls
            if call.tool in MUTATING
        ]
        assert audited == called, task.task.id


WRITTEN_AT = ("created_at", "updated_at", "posted_at")


def _when_written_masked(value: Any) -> Any:
    """``value`` with the times its records were written masked."""
    if isinstance(value, dict):
        return {
            k: "..." if k in WRITTEN_AT else _when_written_masked(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_when_written_masked(item) for item in value]
    return value


def test_the_agents_give_the_same_results_every_time(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    tasks = ["GL-003", "AP-001", "AR-002", "JE-003"]
    for run in ("first", "second"):
        run_benchmark(dataset, ExampleAgents(), tmp_path / run, tasks)
    first, second = tmp_path / "first", tmp_path / "second"
    assert (first / "results.json").read_bytes() == (
        second / "results.json"
    ).read_bytes()
    # A trajectory holds each result as the agent saw it, including when the task's
    # own records, such as the clerk's bill, were written; nothing else differs.
    for task in tasks:
        name = f"trajectories/{task}.jsonl"
        trajectories = [
            [
                _when_written_masked(json.loads(line))
                for line in (run / name).read_text().splitlines()
            ]
            for run in (first, second)
        ]
        assert trajectories[0] == trajectories[1], task

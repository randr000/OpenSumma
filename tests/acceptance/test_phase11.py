"""Phase 11 acceptance: example accounting agents.

Each example agent is run by ``erp benchmark run``, as the installed command in an
empty directory, on every task of the standard dataset, which the first run
generates. Each solves the tasks it has a workflow for and declines the rest; it
works through the tools alone, as the books it leaves behind and their audit logs
show; and everything it changes is audited with its reasons and evidence.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

import opensumma.agents
from opensumma.benchmark import TASKS
from opensumma.datasets import books_fingerprint
from opensumma.db import create_engine
from opensumma.mcp.mutating_tools import TOOLS as MUTATING_TOOLS
from opensumma.mcp.read_tools import TOOLS as READ_TOOLS
from opensumma.workflow import audit_history, get_actor, verify_audit_log

HANDLED = {
    "investigator": {
        "GL-001",
        "GL-002",
        "GL-003",
        "GL-004",
        "GL-005",
        "GL-006",
        "AP-002",
        "AP-004",
        "AR-001",
        "AR-002",
        "CLOSE-001",
    },
    "journal-entry": {"JE-001", "JE-002", "JE-003"},
    "duplicate-invoice": {"AP-001", "AP-003"},
}
TOOLS = {tool.__name__ for tool in READ_TOOLS + MUTATING_TOOLS}
MUTATING = {tool.__name__ for tool in MUTATING_TOOLS}
# What the SYSTEM records in a task's books: the dataset's generation, and the
# workspace's actors and the agent's key. Any other change by the SYSTEM would be a
# change made beneath the workflow.
SETUP = {
    "generate_dataset",
    "create_actor",
    "create_actor_permission",
    "create_api_key",
}


def _erp(*arguments: str, cwd: Path) -> str:
    finished = subprocess.run(
        [str(Path(sys.executable).with_name("erp")), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An empty directory where each example agent ran on the whole benchmark."""
    directory = tmp_path_factory.mktemp("agents")
    for agent in HANDLED:
        _erp("benchmark", "run", "--agent", agent, cwd=directory)
    return directory


def _results(workdir: Path, agent: str) -> dict[str, Any]:
    results: dict[str, Any] = json.loads(
        (workdir / "results" / agent / "results.json").read_text()
    )
    return results


def _tasks(workdir: Path, agent: str) -> dict[str, dict[str, Any]]:
    return {task["id"]: task for task in _results(workdir, agent)["tasks"]}


def _trajectory(workdir: Path, agent: str, task_id: str) -> list[dict[str, Any]]:
    path = workdir / "results" / agent / "trajectories" / f"{task_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@contextmanager
def _books(workdir: Path, agent: str, task_id: str) -> Iterator[Session]:
    path = workdir / "results" / agent / "workspaces" / task_id / "books.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _ground_truth(workdir: Path, error_type: str) -> list[dict[str, Any]]:
    truth = json.loads(
        (workdir / "datasets" / "benchmark" / "ground_truth.json").read_text()
    )
    return [e["details"] for e in truth["errors"] if e["type"] == error_type]


def _declined_the_rest(tasks: dict[str, dict[str, Any]], handled: set[str]) -> None:
    for task_id, task in tasks.items():
        if task_id in handled:
            assert task["score"] == 1.0, task_id
            assert task["agent_error"] is None, task_id
        else:
            assert (task["answer"], task["usage"]["tool_calls"]) == ({}, 0), task_id


def test_example_investigation_agent(workdir: Path) -> None:
    tasks = _tasks(workdir, "investigator")
    _declined_the_rest(tasks, HANDLED["investigator"])
    for task in tasks.values():
        assert task["usage"]["workflow_actions"] == task["usage"]["changes"] == 0

    # What it finds, it explains, citing the records.
    (unusual,) = _ground_truth(workdir, "unusual_transaction")
    answer = tasks["GL-003"]["answer"]
    assert answer["journal_entry_ids"] == [unusual["journal_entry_id"]]
    (finding,) = answer["findings"]
    assert f"journal_entry_id={unusual['journal_entry_id']}" in finding["evidence"]
    assert unusual["merchant"] in finding["reason"]


def test_example_je_agent(workdir: Path) -> None:
    tasks = _tasks(workdir, "journal-entry")
    _declined_the_rest(tasks, HANDLED["journal-entry"])
    for task_id in ("JE-001", "JE-003"):
        details = tasks[task_id]["details"]
        assert tasks[task_id]["workflow"] == 1.0
        # Proposed, found valid, and submitted: it waits for a person to approve it.
        assert (details["status"], details["valid"]) == ("PENDING_APPROVAL", True)
    assert tasks["JE-003"]["details"]["replaced_voided"] is True
    assert (
        tasks["JE-002"]["answer"]["issue_codes"]
        == (tasks["JE-002"]["expected"]["issue_codes"])
    )


def test_example_duplicate_invoice_agent(workdir: Path) -> None:
    tasks = _tasks(workdir, "duplicate-invoice")
    _declined_the_rest(tasks, HANDLED["duplicate-invoice"])
    for task_id, error_type in (
        ("AP-001", "duplicate_invoice"),
        ("AP-003", "duplicate_payment"),
    ):
        duplicates = [
            e["duplicate_accounting_object_id"]
            for e in _ground_truth(workdir, error_type)
        ]
        assert tasks[task_id]["answer"]["accounting_object_ids"] == duplicates


def test_agents_interact_through_tools_rather_than_sql(workdir: Path) -> None:
    # The agents cannot reach a database: nothing of the kind is imported.
    package = Path(opensumma.agents.__file__).parent
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom | ast.Import):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names]
                )
                for name in names:
                    assert not name.startswith(
                        ("sqlalchemy", "sqlite3", "opensumma.db", "opensumma.kernel")
                    ), path.name

    fingerprint = json.loads(
        (workdir / "datasets" / "benchmark" / "manifest.json").read_text()
    )["fingerprint"]
    for agent, handled in HANDLED.items():
        for task_id in sorted(handled):
            trajectory = _trajectory(workdir, agent, task_id)
            calls = trajectory[:-1]
            assert calls and {call["tool"] for call in calls} <= TOOLS
            with _books(workdir, agent, task_id) as session:
                if agent != "journal-entry":
                    # The readers left the books exactly as they found them.
                    assert books_fingerprint(session) == fingerprint, task_id
                # Nothing changed beneath the workflow ...
                system = {e.action for e in audit_history(session) if e.actor is None}
                assert system <= SETUP, task_id
                # ... and every change the agent made is a call to a tool.
                audited = [
                    e.action
                    for e in audit_history(session, actor=get_actor(session, "agent"))
                ]
            assert audited == [c["tool"] for c in calls if c["tool"] in MUTATING]


def test_agent_actions_are_auditable(workdir: Path) -> None:
    summary = _results(workdir, "journal-entry")["summary"]
    assert (summary["auditability"], summary["invalid_posting_rate"]) == (1.0, 0.0)
    for task_id in sorted(HANDLED["journal-entry"]):
        with _books(workdir, "journal-entry", task_id) as session:
            events = audit_history(session, actor=get_actor(session, "agent"))
            assert events, task_id
            for event in events:
                assert event.actor_type.value == "AGENT"
                assert event.result.value == "SUCCEEDED"
                assert event.reason and event.evidence, (task_id, event.action)
            assert verify_audit_log(session).is_intact

    # The trajectory records every call, reads included, with what came back.
    *calls, final = _trajectory(workdir, "journal-entry", "JE-001")
    assert [c["tool"] for c in calls[-3:]] == [
        "propose_journal_entry",
        "validate_journal_entry",
        "submit_for_approval",
    ]
    assert calls[0]["tool"] == "get_accounting_object"
    assert final["answer"] == {"journal_entry_id": calls[-1]["result"]["id"]}


def test_benchmark_can_evaluate_agents(workdir: Path) -> None:
    solved: set[str] = set()
    for agent, handled in HANDLED.items():
        output = workdir / "results" / agent
        results = _results(workdir, agent)
        assert results["agent"] == agent
        assert (output / "results.csv").is_file()
        assert len(list((output / "trajectories").iterdir())) == len(TASKS)
        summary = results["summary"]
        assert summary["accounting_correctness"] == round(len(handled) / len(TASKS), 4)
        assert (summary["tool_use_accuracy"], summary["hallucination_rate"]) == (
            1.0,
            0.0,
        )
        solved |= {t["id"] for t in results["tasks"] if t["score"] == 1.0}
    # Together, the three agents solve every task.
    assert solved == {task.id for task in TASKS}

    # An agent is evaluated the same way every time.
    _erp(
        "benchmark",
        "run",
        "--agent",
        "duplicate-invoice",
        "--dataset",
        "datasets/benchmark",
        "--output",
        "again",
        cwd=workdir,
    )
    assert (workdir / "again" / "results.json").read_bytes() == (
        workdir / "results" / "duplicate-invoice" / "results.json"
    ).read_bytes()

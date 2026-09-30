"""Phase 10 acceptance: the benchmark and evaluation framework.

``erp benchmark run`` is run as the installed command in an empty directory: it
generates the standard dataset and runs the oracle, whose reference solutions solve
every task through the tools. Then a custom agent, a module of its own named on the
command line, answers two tasks through the tools and nothing else, and is scored
exactly: full marks on those two, none on the rest, with every call recorded.
"""

import csv
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from opensumma.benchmark import TASKS

SPECIFIED = (
    "GL-001",
    "GL-002",
    "GL-003",
    "GL-004",
    "AP-001",
    "AP-002",
    "AR-001",
    "CLOSE-001",
    "JE-001",
    "JE-002",
    "JE-003",
)
METRICS = (
    "accounting_correctness",
    "numerical_correctness",
    "classification_accuracy",
    "workflow_accuracy",
    "tool_use_accuracy",
    "hallucination_rate",
    "invalid_posting_rate",
    "auditability",
)
READER = '''
import re

DAY = r"end of (\\d{4}-\\d{2}-\\d{2})"


class Reader:
    """Answers the balance tasks from the tools, and nothing else."""

    name = "reader"

    def run(self, task, tools):
        if task.id == "GL-001":
            code = re.search(r"account (\\d+)", task.instructions).group(1)
            day = re.search(DAY, task.instructions).group(1)
            balance = tools.call("get_account_balance", code=code, as_of=day)
            return {"balance": balance.result["balance"]}
        if task.id == "GL-002":
            day = re.search(DAY, task.instructions).group(1)
            report = tools.call("get_trial_balance", as_of=day).result
            lines = [
                {"account": row["account_code"], "debit": row["debit"],
                 "credit": row["credit"]}
                for row in report["lines"]
            ]
            return {
                "lines": lines,
                "total_debits": report["total_debits"],
                "total_credits": report["total_credits"],
            }
        return {}
'''


def _erp(*arguments: str, cwd: Path, **environment: str) -> str:
    finished = subprocess.run(
        [str(Path(sys.executable).with_name("erp")), *arguments],
        cwd=cwd,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An empty directory where ``erp benchmark run`` ran, then a custom agent."""
    directory = tmp_path_factory.mktemp("bench")
    _erp("benchmark", "run", cwd=directory)
    (directory / "reader.py").write_text(textwrap.dedent(READER))
    _erp(
        "benchmark",
        "run",
        "--agent",
        "reader:Reader",
        cwd=directory,
        PYTHONPATH=str(directory),
    )
    return directory


def _results(directory: Path) -> dict[str, Any]:
    results: dict[str, Any] = json.loads((directory / "results.json").read_text())
    return results


def test_benchmark_task_schema_exists() -> None:
    for task in TASKS:
        described = task.describe()
        assert described["id"] == task.id and described["title"]
        assert described["category"] in {
            "general_ledger",
            "accounts_payable",
            "accounts_receivable",
            "close",
            "journal_entries",
        }
        schema = described["answer_schema"]
        assert schema["type"] == "object" and schema["properties"]
        assert json.loads(json.dumps(described)) == described


def test_at_least_10_benchmark_tasks_exist() -> None:
    ids = [task.id for task in TASKS]
    assert len(ids) >= 10
    assert set(SPECIFIED) <= set(ids)


def test_benchmark_cli_works(workdir: Path) -> None:
    assert (workdir / "datasets" / "benchmark" / "books.db").is_file()
    assert sorted(p.name for p in (workdir / "results").iterdir()) == [
        "oracle",
        "reader",
    ]


def test_deterministic_scoring_exists(workdir: Path) -> None:
    oracle = _results(workdir / "results" / "oracle")
    assert {task["id"]: task["score"] for task in oracle["tasks"]} == dict.fromkeys(
        (task.id for task in TASKS), 1.0
    )

    reader = _results(workdir / "results" / "reader")
    scores = {task["id"]: task["score"] for task in reader["tasks"]}
    assert (scores.pop("GL-001"), scores.pop("GL-002")) == (1.0, 1.0)
    assert set(scores.values()) == {0.0}
    assert reader["summary"]["accounting_correctness"] == round(2 / len(TASKS), 4)
    assert reader["summary"]["numerical_correctness"] == 0.5  # 2 of the 4 numeric

    # The same agent, on the same dataset, gets the same results.
    _erp("benchmark", "run", "--output", "again", cwd=workdir)
    again = (workdir / "again" / "results.json").read_bytes()
    assert again == (workdir / "results" / "oracle" / "results.json").read_bytes()


def test_agent_trajectories_can_be_recorded(workdir: Path) -> None:
    trajectories = workdir / "results" / "reader" / "trajectories"
    *calls, final = [
        json.loads(line)
        for line in (trajectories / "GL-001.jsonl").read_text().splitlines()
    ]
    (call,) = calls
    assert call["tool"] == "get_account_balance" and call["error"] is None
    assert final["answer"] == {"balance": call["result"]["balance"]}

    # A task the agent did not attempt leaves its answer, and why it scored nothing.
    lines = (trajectories / "JE-001.jsonl").read_text().splitlines()
    (only,) = [json.loads(line) for line in lines]
    assert only["answer"] == {} and only["agent_error"] is None
    assert "journal_entry_id: Field required" in only["answer_error"]


def test_results_can_be_exported(workdir: Path) -> None:
    output = workdir / "results" / "reader"
    results = _results(output)
    assert results["agent"] == "reader"
    assert results["dataset"]["company"] == "benchmark"
    assert set(METRICS) <= set(results["summary"])
    with (output / "results.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["id"] for row in rows] == [task.id for task in TASKS]
    assert {"score", "tool_calls", "hallucinated_references"} <= set(rows[0])
    assert rows[0]["score"] == "1.0" and rows[0]["tool_calls"] == "1"

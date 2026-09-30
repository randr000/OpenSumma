"""Running the benchmark: tasks in their own workspaces, agents working through the
tools, and deterministic scores, metrics, and trajectories."""

import hashlib
import json
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from opensumma.benchmark import (
    TASKS,
    BenchmarkResult,
    NullAgent,
    OracleAgent,
    TaskPrompt,
    Tools,
    load_dataset,
    run_benchmark,
)
from opensumma.benchmark.environment import DatasetFiles
from opensumma.datasets import books_fingerprint, cast, write_dataset
from opensumma.db import create_engine
from opensumma.workflow import audit_history

Answering = Callable[[TaskPrompt, Tools], Any]


class Scripted:
    """An agent that answers each task with a script, and nothing otherwise."""

    name = "scripted"

    def __init__(self, **scripts: Answering) -> None:
        self.scripts = {task_id.replace("_", "-"): s for task_id, s in scripts.items()}

    def run(self, task: TaskPrompt, tools: Tools) -> Any:
        script = self.scripts.get(task.id)
        return {} if script is None else script(task, tools)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> DatasetFiles:
    directory = tmp_path_factory.mktemp("dataset") / "acme"
    write_dataset(directory, company="acme", transactions=400, seed=7, errors=22)
    return load_dataset(directory)


@pytest.fixture(scope="module")
def oracle(
    dataset: DatasetFiles, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, BenchmarkResult]:
    output = tmp_path_factory.mktemp("oracle")
    return output, run_benchmark(dataset, OracleAgent(), output)


def _run(dataset: DatasetFiles, tmp_path: Path, agent: Any, *task_ids: str) -> Any:
    result = run_benchmark(dataset, agent, tmp_path / "results", list(task_ids))
    return result.tasks[0] if len(task_ids) == 1 else result


def _errors(dataset: DatasetFiles, error_type: str) -> list[dict[str, Any]]:
    return [e for e in dataset.ground_truth["errors"] if e["type"] == error_type]


def _object_id(task: TaskPrompt) -> int:
    found = re.search(r"accounting object (\d+)", task.instructions)
    assert found is not None
    return int(found.group(1))


def test_the_oracle_solves_every_task_through_the_tools(
    dataset: DatasetFiles, oracle: tuple[Path, BenchmarkResult]
) -> None:
    _, result = oracle
    assert [t.task.id for t in result.tasks] == [task.id for task in TASKS]
    for task in result.tasks:
        assert (task.score.score, task.answer_error, task.agent_error) == (
            1.0,
            None,
            None,
        ), task.task.id
    assert result.summary() == {
        "tasks": len(TASKS),
        "accounting_correctness": 1.0,
        "numerical_correctness": 1.0,
        "classification_accuracy": 1.0,
        "workflow_accuracy": 1.0,
        "tool_use_accuracy": 1.0,
        "hallucination_rate": 0.0,
        "invalid_posting_rate": 0.0,
        "auditability": 1.0,
        "tool_calls": 8,
    }


def test_a_run_leaves_the_dataset_as_it_was(
    dataset: DatasetFiles, oracle: tuple[Path, BenchmarkResult]
) -> None:
    output, _ = oracle
    engine = create_engine(f"sqlite:///{dataset.books}")
    with Session(engine) as session:
        assert books_fingerprint(session) == dataset.manifest["fingerprint"]
    engine.dispose()
    workspaces = sorted(p.name for p in (output / "workspaces").iterdir())
    assert workspaces == sorted(task.id for task in TASKS)


def test_the_null_agent_scores_nothing(dataset: DatasetFiles, tmp_path: Path) -> None:
    result = _run(dataset, tmp_path, NullAgent(), *(task.id for task in TASKS))
    summary = result.summary()
    assert summary["accounting_correctness"] == 0.0
    assert summary["tool_calls"] == 0
    errors = {t.task.id: t.answer_error for t in result.tasks if t.answer_error}
    assert set(errors) == {"GL-001", "GL-002", "AR-001", "JE-001", "JE-003"}
    assert "balance: Field required" in errors["GL-001"]


def test_the_same_agent_gets_the_same_results(
    dataset: DatasetFiles, oracle: tuple[Path, BenchmarkResult], tmp_path: Path
) -> None:
    first, _ = oracle
    run_benchmark(dataset, OracleAgent(), tmp_path)
    for name in ["results.json", "results.csv"] + [
        f"trajectories/{task.id}.jsonl" for task in TASKS
    ]:
        assert (tmp_path / name).read_bytes() == (first / name).read_bytes(), name


def test_the_benchmark_is_the_same_on_every_machine(tmp_path: Path) -> None:
    # Pinned, on the dataset the generator's own pinned test uses: a change to any
    # task's wording, choices, expected answer, or scoring changes this, and must
    # bump BENCHMARK_VERSION and update the value.
    write_dataset(tmp_path / "dataset", company="acme", transactions=300, seed=2026)
    run_benchmark(tmp_path / "dataset", OracleAgent(), tmp_path / "results")
    results = (tmp_path / "results" / "results.json").read_bytes()
    assert hashlib.sha256(results).hexdigest() == (
        "16bcb5a3a9fac270aaba43205923a8d657bcc53556b68ce0d23e36d388c42985"
    )


def test_results_are_exported_as_json_and_csv(
    oracle: tuple[Path, BenchmarkResult],
) -> None:
    output, result = oracle
    document = json.loads((output / "results.json").read_text())
    assert document == result.to_json()
    assert document["agent"] == "oracle"
    assert document["dataset"]["fingerprint"].startswith("sha256:")
    rows = (output / "results.csv").read_text().splitlines()
    assert rows[0].startswith("id,category,score,")
    assert [row.split(",")[0] for row in rows[1:]] == [task.id for task in TASKS]


def test_trajectories_record_every_call_and_the_answer(
    oracle: tuple[Path, BenchmarkResult],
) -> None:
    output, result = oracle
    lines = (output / "trajectories" / "JE-003.jsonl").read_text().splitlines()
    *calls, final = [json.loads(line) for line in lines]
    assert [(c["step"], c["tool"]) for c in calls] == [
        (1, "void_journal_entry"),
        (2, "propose_journal_entry"),
    ]
    assert calls[0]["result"]["status"] == "VOIDED" and calls[0]["error"] is None
    (je_003,) = [t for t in result.tasks if t.task.id == "JE-003"]
    assert final == {"answer": je_003.answer, "answer_error": None, "agent_error": None}


def test_a_partial_answer_is_scored_by_what_it_found(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    duplicates = [
        e["details"]["duplicate_accounting_object_id"]
        for e in _errors(dataset, "duplicate_invoice")
    ]
    assert len(duplicates) == 2
    half = Scripted(
        AP_001=lambda task, tools: {"accounting_object_ids": duplicates[:1]}
    )
    assert _run(dataset, tmp_path / "half", half, "AP-001").score.score == (
        pytest.approx(2 / 3)
    )
    noisy = Scripted(
        AP_001=lambda task, tools: {"accounting_object_ids": [duplicates[0], 1]}
    )
    assert _run(dataset, tmp_path / "noisy", noisy, "AP-001").score.score == 0.5


def test_classification_counts_errors_found_and_corrected(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    misposted = [e["details"] for e in _errors(dataset, "wrong_gl_account")]
    answer = {
        "items": [
            {
                "journal_entry_id": misposted[0]["journal_entry_id"],
                "correct_account": misposted[0]["correct_account"],
            },
            {
                "journal_entry_id": misposted[1]["journal_entry_id"],
                "correct_account": misposted[1]["posted_account"],
            },
        ]
    }
    result = _run(
        dataset, tmp_path, Scripted(GL_004=lambda task, tools: answer), "GL-004"
    )
    assert result.score.score == 0.5
    assert (result.score.classified, result.score.correctly_classified) == (2, 1)


def test_misused_tools_and_refused_postings_are_counted(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    def clumsy(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        tools.call("get_balance", code="6100")  # no such tool
        tools.call("get_account", code=6100)  # a number where a code is text
        tools.call("get_account", code="9999")  # no such account
        tools.call("post_journal_entry", entry_id=1)  # already posted, and no right
        good = tools.call("get_account_balance", code="6100")
        return {"balance": good.result["balance"]}

    result = _run(dataset, tmp_path, Scripted(GL_001=clumsy), "GL-001")
    calls = result.calls
    assert [(c.tool, c.rejected, c.refused) for c in calls] == [
        ("get_balance", True, False),
        ("get_account", True, False),
        ("get_account", False, True),
        ("post_journal_entry", False, True),
        ("get_account_balance", False, False),
    ]
    assert calls[2].error["error"] == "UnknownAccountError"
    usage = result.usage
    assert (usage.tool_calls, usage.rejected_calls, usage.unknown_record_calls) == (
        5,
        2,
        1,
    )
    assert (usage.workflow_actions, usage.refused_workflow_actions) == (1, 1)


def test_answers_naming_records_that_do_not_exist_are_hallucinations(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    (unusual,) = _errors(dataset, "unusual_transaction")[:1]
    real = unusual["details"]["journal_entry_id"]
    agent = Scripted(GL_003=lambda task, tools: {"journal_entry_ids": [real, 999_999]})
    usage = _run(dataset, tmp_path, agent, "GL-003").usage
    assert (usage.references, usage.hallucinated_references) == (2, 1)


def test_an_agent_that_fails_scores_zero_and_says_why(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    def crash(task: TaskPrompt, tools: Tools) -> Any:
        raise RuntimeError("out of ideas")

    crashed = _run(dataset, tmp_path / "crash", Scripted(GL_001=crash), "GL-001")
    assert crashed.agent_error == "RuntimeError: out of ideas"
    assert crashed.score.score == 0.0

    def decimal(task: TaskPrompt, tools: Tools) -> Any:
        return {"balance": Decimal("1.00")}

    unencodable = _run(
        dataset, tmp_path / "decimal", Scripted(GL_001=decimal), "GL-001"
    )
    assert unencodable.agent_error is not None
    assert unencodable.agent_error.startswith("TypeError")


def _bill_entry(tools: Tools, object_id: int, **changes: Any) -> dict[str, Any]:
    """The entry a careful agent proposes for a bill, with ``changes``."""
    bill = tools.call("get_accounting_object", object_id=object_id).result
    data = bill["data"]
    vendor = next(v for v in cast.VENDORS if v.code == bill["counterparty"])
    tags = {"DEPARTMENT": data["department"], "LOCATION": data["location"]}
    entry = {
        "entry_date": data["invoice_date"],
        "description": f"{data['vendor_name']} {data['invoice_number']}",
        "lines": [
            {"account": vendor.account, "debit": data["amount"], "dimensions": tags},
            {"account": "2110", "credit": data["amount"]},
        ],
        "accounting_object_id": object_id,
        "reason": "Posted as this vendor's bills are",
        "evidence": [f"accounting_object_id={object_id}"],
    }
    return {**entry, **changes}


def test_a_proposed_entry_is_scored_line_by_line_and_in_the_workflow(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    def wrong_account(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        entry = _bill_entry(tools, _object_id(task))
        entry["lines"][0]["account"] = "6900"
        return {
            "journal_entry_id": tools.call("propose_journal_entry", **entry).result[
                "id"
            ]
        }

    result = _run(dataset, tmp_path / "wrong", Scripted(JE_001=wrong_account), "JE-001")
    assert (result.score.score, result.score.workflow) == (0.5, 1.0)
    assert (result.usage.changes, result.usage.documented_changes) == (1, 1)

    def unlinked(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        entry = _bill_entry(tools, _object_id(task), accounting_object_id=None)
        del entry["reason"], entry["evidence"]
        return {
            "journal_entry_id": tools.call("propose_journal_entry", **entry).result[
                "id"
            ]
        }

    result = _run(dataset, tmp_path / "unlinked", Scripted(JE_001=unlinked), "JE-001")
    assert (result.score.score, result.score.workflow) == (0.0, 0.0)
    assert result.score.details["problem"] == "it does not record the bill"
    assert (result.usage.changes, result.usage.documented_changes) == (1, 0)


def test_a_correction_must_replace_the_invalid_entry(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    def without_voiding(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        entry = _bill_entry(tools, _object_id(task))
        return {
            "journal_entry_id": tools.call("propose_journal_entry", **entry).result[
                "id"
            ]
        }

    result = _run(dataset, tmp_path, Scripted(JE_003=without_voiding), "JE-003")
    assert (result.score.score, result.score.workflow) == (1.0, 0.0)
    assert result.score.details["replaced_voided"] is False


def test_the_agent_acts_as_a_registered_agent_that_may_only_propose(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    def overreach(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        entry_id = int(re.findall(r"Journal entry (\d+)", task.instructions)[0])
        tools.call("submit_for_approval", entry_id=entry_id, reason="Looks fine")
        tools.call("approve_journal_entry", entry_id=entry_id, reason="Looks fine")
        return {"issue_codes": []}

    result = _run(dataset, tmp_path, Scripted(JE_002=overreach), "JE-002")
    assert [c.error["error"] for c in result.calls if c.error] == [
        "JournalEntryError",
        "InvalidTransitionError",
    ]
    assert (result.usage.workflow_actions, result.usage.refused_workflow_actions) == (
        2,
        2,
    )
    assert result.score.workflow == 0.0  # it never validated the entry

    engine = create_engine(
        f"sqlite:///{tmp_path / 'results/workspaces/JE-002/books.db'}"
    )
    with Session(engine) as session:
        refused = audit_history(session, result="REFUSED")
        assert [(e.actor.code, e.actor_type.value) for e in refused if e.actor] == [
            ("agent", "AGENT"),
            ("agent", "AGENT"),
        ]
    engine.dispose()


def test_tools_describe_themselves(dataset: DatasetFiles, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def look(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        seen["specs"] = tools.specs
        seen["schema"] = task.answer_schema
        return {}

    _run(dataset, tmp_path, Scripted(CLOSE_001=look), "CLOSE-001")
    specs = {spec.name: spec for spec in seen["specs"]}
    assert len(specs) == 29
    assert specs["get_trial_balance"].read_only
    assert not specs["propose_journal_entry"].read_only
    assert specs["propose_journal_entry"].input_schema["additionalProperties"] is False
    assert seen["schema"]["properties"]["periods"]["type"] == "array"


def test_results_go_to_a_new_or_empty_directory(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    (tmp_path / "old.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        run_benchmark(dataset, NullAgent(), tmp_path, ["GL-001"])


def test_a_dataset_needs_its_own_ground_truth(
    dataset: DatasetFiles, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path)
    for name in ("books.db", "manifest.json", "ground_truth.json"):
        (tmp_path / name).write_bytes((dataset.directory / name).read_bytes())
    truth = json.loads((tmp_path / "ground_truth.json").read_text())
    truth["fingerprint"] = "sha256:other"
    (tmp_path / "ground_truth.json").write_text(json.dumps(truth))
    with pytest.raises(ValueError, match="other books"):
        load_dataset(tmp_path)

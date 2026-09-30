"""The benchmark's pure parts: the task catalogue, scoring arithmetic, and how the
summary pools each task's metrics."""

import json
from decimal import Decimal
from typing import Any

import pytest

from opensumma.benchmark import (
    TASKS,
    BenchmarkResult,
    NullAgent,
    OracleAgent,
    Score,
    TaskResult,
    Usage,
    get_task,
    load_agent,
)
from opensumma.benchmark.runner import _references
from opensumma.benchmark.schema import amount, dice

SPECIFIED = {
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
}


def test_the_catalogue_has_the_specified_tasks_and_more() -> None:
    ids = [task.id for task in TASKS]
    assert len(ids) == len(set(ids)) >= 10
    assert set(ids) >= SPECIFIED
    assert get_task("JE-002").title == "Validate a journal entry"
    with pytest.raises(KeyError):
        get_task("XX-999")


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_every_task_describes_itself_as_json(task: Any) -> None:
    described = task.describe()
    assert json.loads(json.dumps(described)) == described
    assert set(described) == {
        "id",
        "title",
        "category",
        "description",
        "measures",
        "permissions",
        "answer_schema",
    }
    schema = described["answer_schema"]
    assert schema["type"] == "object" and schema["properties"]
    assert described["permissions"] == ["READ_ONLY", "PROPOSER"]
    assert set(described["measures"]) <= {"numerical", "classification", "workflow"}


def test_answers_take_amounts_as_strings_and_ignore_extra_fields() -> None:
    answer = get_task("GL-001").answer
    parsed = answer.model_validate({"balance": "10.5", "note": "checked"})
    assert parsed.model_dump() == {"balance": "10.5"}
    with pytest.raises(ValueError):
        answer.model_validate({"balance": 10.5})
    with pytest.raises(ValueError):
        answer.model_validate({"balance": "ten"})


def test_dice_is_the_f1_score_of_the_items_found() -> None:
    assert dice(set(), set()) == 1.0
    assert dice({1, 2}, set()) == 0.0
    assert dice(set(), {1}) == 0.0
    assert dice({1, 2, 3, 4}, {1, 2}) == pytest.approx(2 / 3)
    assert dice({1, 2}, {1, 2, 3, 4}) == pytest.approx(2 / 3)
    assert dice({1, 2}, {2, 1}) == 1.0


def test_amounts_are_compared_to_the_cent() -> None:
    assert amount("12.5") == amount("12.50") == Decimal("12.50")
    assert amount("-3") == Decimal("-3.00")


def test_the_records_an_answer_names_are_found_wherever_they_are() -> None:
    answer = {
        "items": [
            {"journal_entry_id": 4, "correct_account": "6500"},
            {"journal_entry_id": 9, "correct_account": "6700"},
        ],
        "accounting_object_ids": [12],
        "periods": ["2026-03"],
        "total": "10.00",
    }
    assert _references(answer) == [
        ("journal_entry", 4),
        ("account", "6500"),
        ("journal_entry", 9),
        ("account", "6700"),
        ("accounting_object", 12),
        ("period", "2026-03"),
    ]


def _result(task_id: str, score: Score, **usage: int) -> TaskResult:
    return TaskResult(
        get_task(task_id), "", {}, {}, None, None, score, Usage(**usage), ()
    )


def test_the_summary_pools_every_task() -> None:
    result = BenchmarkResult(
        "tester",
        {},
        (
            _result(
                "GL-001", Score(1.0, numerical=1.0), tool_calls=4, rejected_calls=1
            ),
            _result(
                "GL-004",
                Score(0.5, classified=4, correctly_classified=3),
                tool_calls=6,
                unknown_record_calls=1,
                references=10,
                hallucinated_references=2,
            ),
            _result(
                "JE-001",
                Score(0.0, workflow=0.0),
                workflow_actions=4,
                refused_workflow_actions=1,
                changes=3,
                documented_changes=2,
            ),
        ),
    )
    assert result.summary() == {
        "tasks": 3,
        "accounting_correctness": 0.5,
        "numerical_correctness": 1.0,
        "classification_accuracy": 0.75,
        "workflow_accuracy": 0.0,
        "tool_use_accuracy": 0.8,
        "hallucination_rate": 0.2,
        "invalid_posting_rate": 0.25,
        "auditability": 0.6667,
        "tool_calls": 10,
    }


def test_a_metric_nothing_measured_is_not_scored() -> None:
    result = BenchmarkResult("tester", {}, (_result("GL-003", Score(0.0)),))
    summary = result.summary()
    for metric in (
        "numerical_correctness",
        "classification_accuracy",
        "workflow_accuracy",
        "tool_use_accuracy",
        "hallucination_rate",
        "invalid_posting_rate",
        "auditability",
    ):
        assert summary[metric] is None, metric


def test_agents_are_loaded_by_name_or_import_path() -> None:
    assert isinstance(load_agent("null"), NullAgent)
    assert isinstance(load_agent("oracle"), OracleAgent)
    assert isinstance(load_agent("opensumma.benchmark.agents:NullAgent"), NullAgent)
    with pytest.raises(ValueError, match="built-in agents are"):
        load_agent("gpt")
    for not_an_agent in ("opensumma.utc:utcnow", "opensumma.benchmark.runner:FORMAT"):
        with pytest.raises(TypeError, match="not an agent"):
            load_agent(not_an_agent)

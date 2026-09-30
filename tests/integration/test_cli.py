"""The ``erp`` command line."""

import json
from pathlib import Path

import pytest

from opensumma.cli import main

GENERATE = ["dataset", "generate", "--company", "acme", "--transactions", "300"]


def test_generate_writes_a_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "acme-1"
    assert main([*GENERATE, "--seed", "1", "--output", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    ground_truth = json.loads((output / "ground_truth.json").read_text())
    assert (output / "books.db").is_file()
    assert manifest["seed"] == 1 and len(ground_truth["errors"]) == 11

    printed = capsys.readouterr().out
    assert "Generated acme: 300 transactions in 2026" in printed
    assert "11 injected errors" in printed
    assert manifest["fingerprint"] in printed


def test_generate_writes_under_datasets_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main([*GENERATE, "--seed", "1", "--errors", "0"]) == 0
    ground_truth = json.loads(
        (tmp_path / "datasets" / "acme" / "ground_truth.json").read_text()
    )
    assert ground_truth["errors"] == []


def test_generate_will_not_overwrite_a_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = [*GENERATE, "--seed", "1", "--output", str(tmp_path)]
    assert main(arguments) == 0
    capsys.readouterr()
    assert main(arguments) == 1
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--transactions", "10"], "300 to"),
        (["--errors", "5000"], "at most"),
        (["--company", "Acme Corp"], "lowercase"),
    ],
)
def test_parameters_it_cannot_meet_are_usage_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    with pytest.raises(SystemExit) as exit_:
        main([*GENERATE, "--seed", "1", "--output", str(tmp_path / "x"), *arguments])
    assert exit_.value.code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_a_seed_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(GENERATE)
    assert "--seed" in capsys.readouterr().err


# --- erp benchmark --------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("cli") / "acme"
    assert main([*GENERATE, "--seed", "3", "--output", str(directory)]) == 0
    return directory


def test_benchmark_tasks_lists_every_task(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["benchmark", "tasks"]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed[0].split()[0] == "GL-001" and len(listed) == 16

    assert main(["benchmark", "tasks", "--json"]) == 0
    tasks = json.loads(capsys.readouterr().out)
    assert tasks[-1]["id"] == "JE-003" and "answer_schema" in tasks[-1]


def test_benchmark_run_writes_results(
    small_dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    arguments = ["benchmark", "run", "--dataset", str(small_dataset)]
    assert main([*arguments, "--tasks", "GL-001,JE-002", "--output", str(output)]) == 0
    printed = capsys.readouterr().out
    assert "Ran agent oracle on 2 tasks" in printed
    assert "accounting correctness   1.0" in printed
    results = json.loads((output / "results.json").read_text())
    assert [task["id"] for task in results["tasks"]] == ["GL-001", "JE-002"]
    assert (output / "trajectories" / "JE-002.jsonl").is_file()

    assert main([*arguments, "--tasks", "GL-001", "--output", str(output)]) == 1
    assert "not empty" in capsys.readouterr().err


def test_benchmark_run_runs_the_example_agents_by_name(
    small_dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    arguments = ["benchmark", "run", "--dataset", str(small_dataset), "--agent"]
    tasks = ["--tasks", "AP-001,JE-002", "--output", str(output)]
    assert main([*arguments, "examples", *tasks]) == 0
    printed = capsys.readouterr().out
    assert "Ran agent examples on 2 tasks" in printed
    assert "accounting correctness   1.0" in printed
    assert json.loads((output / "results.json").read_text())["agent"] == "examples"


def test_benchmark_run_numbers_its_default_output(
    small_dataset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for _ in range(2):
        arguments = [
            "--dataset",
            str(small_dataset),
            "--agent",
            "null",
            "--tasks",
            "GL-003",
        ]
        assert main(["benchmark", "run", *arguments]) == 0
    assert sorted(p.name for p in (tmp_path / "results").iterdir()) == [
        "null",
        "null-2",
    ]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--tasks", "GL-001,XX-9"], "no such tasks: XX-9"),
        (["--agent", "gpt"], "cannot load agent"),
        (["--agent", "no.such.module:Agent"], "cannot load agent"),
    ],
)
def test_benchmark_run_refuses_what_it_cannot_do(
    small_dataset: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["benchmark", "run", "--dataset", str(small_dataset), *arguments])
    assert exit_.value.code == 2
    assert message in capsys.readouterr().err


def test_benchmark_run_needs_a_dataset_where_it_is_told(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(["benchmark", "run", "--dataset", str(tmp_path / "none")])
    assert "holds no dataset" in capsys.readouterr().err

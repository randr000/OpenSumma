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

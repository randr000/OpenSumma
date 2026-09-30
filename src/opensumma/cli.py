"""The ``erp`` command line.

``erp dataset generate --company acme --transactions 10000 --seed 42`` writes a
deterministic dataset: the company's books, a manifest, and the ground truth of the
errors injected into them.

``erp benchmark run`` runs an agent on every benchmark task against a dataset and
writes the results; ``erp benchmark tasks`` lists the tasks. ``python -m opensumma``
runs the same commands.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from opensumma.benchmark import (
    RESULTS_CSV,
    RESULTS_FILE,
    TASKS,
    TRAJECTORIES,
    load_agent,
    load_dataset,
    run_benchmark,
)
from opensumma.benchmark.environment import write_standard_dataset
from opensumma.datasets import (
    BOOKS_FILE,
    DEFAULT_YEAR,
    GROUND_TRUTH_FILE,
    MANIFEST_FILE,
    write_dataset,
)

STANDARD_DATASET_DIRECTORY = Path("datasets") / "benchmark"
RESULTS_DIRECTORY = Path("results")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="erp",
        description="OpenSumma: an accounting environment for AI accounting agents.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    datasets = commands.add_parser("dataset", help="deterministic accounting datasets")
    dataset_commands = datasets.add_subparsers(dest="dataset_command", required=True)
    generate = dataset_commands.add_parser(
        "generate",
        help="generate a company's books with known errors in them",
        description="Generate a company's year of books, a manifest, and the ground "
        "truth of the errors injected into them. The same parameters always give "
        "the same dataset.",
    )
    generate.add_argument("--company", required=True, help="a name such as 'acme'")
    generate.add_argument(
        "--transactions",
        type=int,
        required=True,
        help="journal entries in the clean books, 300 or more",
    )
    generate.add_argument("--seed", type=int, required=True)
    generate.add_argument(
        "--errors",
        type=int,
        help="errors to inject; by default one per 100 transactions and at least "
        "one of each type; 0 for clean books",
    )
    generate.add_argument("--year", type=int, default=DEFAULT_YEAR)
    generate.add_argument(
        "--output",
        type=Path,
        help="the directory to write; defaults to datasets/<company>",
    )

    benchmark = commands.add_parser("benchmark", help="evaluate accounting agents")
    benchmark_commands = benchmark.add_subparsers(
        dest="benchmark_command", required=True
    )
    run = benchmark_commands.add_parser(
        "run",
        help="run an agent on the benchmark's tasks",
        description="Run an agent on every benchmark task, or on those named, and "
        "write its scores, metrics, and trajectories.",
    )
    run.add_argument(
        "--dataset",
        type=Path,
        help=f"a generated dataset; by default {STANDARD_DATASET_DIRECTORY}, "
        "generated there if it is missing",
    )
    run.add_argument(
        "--agent",
        default="oracle",
        help="'oracle' (the reference solutions, a calibration), 'null', or "
        "module:attribute; default oracle",
    )
    run.add_argument("--tasks", help="task ids separated by commas; default all")
    run.add_argument(
        "--output",
        type=Path,
        help=f"a new or empty directory; by default a new one in {RESULTS_DIRECTORY}/",
    )
    tasks = benchmark_commands.add_parser("tasks", help="list the benchmark's tasks")
    tasks.add_argument("--json", action="store_true", help="each task's full schema")

    args = parser.parse_args(argv)
    if args.command == "dataset":
        return _generate(args, generate)
    if args.benchmark_command == "run":
        return _run(args, run)
    return _tasks(args)


def _generate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    output: Path = args.output or Path("datasets") / args.company
    try:
        dataset = write_dataset(
            output,
            company=args.company,
            transactions=args.transactions,
            seed=args.seed,
            errors=args.errors,
            year=args.year,
        )
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    except FileExistsError as error:
        print(f"erp: {error}", file=sys.stderr)
        return 1

    manifest, ground_truth = dataset.manifest, dataset.ground_truth
    counts = manifest["counts"]
    print(
        f"Generated {args.company}: {args.transactions:,} transactions in "
        f"{args.year}, {counts['journal_entries']:,} journal entries in the books, "
        f"{len(ground_truth['errors'])} injected errors."
    )
    print(f"  books:        {output / BOOKS_FILE}")
    print(f"  manifest:     {output / MANIFEST_FILE}")
    print(f"  ground truth: {output / GROUND_TRUTH_FILE}")
    print(f"  fingerprint:  {manifest['fingerprint']}")
    return 0


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        agent = load_agent(args.agent)
    except (ImportError, AttributeError, ValueError, TypeError) as error:
        parser.error(f"cannot load agent {args.agent!r}: {error}")
    task_ids = None
    if args.tasks:
        task_ids = [task_id.strip() for task_id in args.tasks.split(",")]
        unknown = sorted(set(task_ids) - {task.id for task in TASKS})
        if unknown:
            parser.error(f"no such tasks: {', '.join(unknown)}")

    directory: Path = args.dataset or STANDARD_DATASET_DIRECTORY
    if args.dataset is None and not directory.exists():
        write_standard_dataset(directory)
        print(f"Generated the standard benchmark dataset in {directory}.")
    try:
        dataset = load_dataset(directory)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))

    output: Path = args.output or _new_directory(RESULTS_DIRECTORY / _slug(agent.name))
    try:
        result = run_benchmark(dataset, agent, output, task_ids)
    except FileExistsError as error:
        print(f"erp: {error}", file=sys.stderr)
        return 1

    print(f"Ran agent {agent.name} on {len(result.tasks)} tasks in {directory}.")
    for task in result.tasks:
        problem = task.agent_error or task.answer_error or ""
        print(
            f"  {task.task.id:<10} {task.score.score:>6.2f}  {problem}"[:100].rstrip()
        )
    print("Summary:")
    for name, value in result.summary().items():
        shown = "n/a" if value is None else value
        print(f"  {name.replace('_', ' '):<24} {shown}")
    print(f"  results:      {output / RESULTS_FILE}, {output / RESULTS_CSV}")
    print(f"  trajectories: {output / TRAJECTORIES}/")
    return 0


def _tasks(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([task.describe() for task in TASKS], indent=2))
        return 0
    for task in TASKS:
        print(f"{task.id:<10} {task.category:<20} {task.title}")
    return 0


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"


def _new_directory(base: Path) -> Path:
    """``base``, or ``base-2``, ``base-3``, ...: the first that does not exist."""
    candidate, number = base, 1
    while candidate.exists():
        number += 1
        candidate = base.with_name(f"{base.name}-{number}")
    return candidate

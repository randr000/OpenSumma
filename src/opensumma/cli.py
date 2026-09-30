"""The ``erp`` command line.

``erp dataset generate --company acme --transactions 10000 --seed 42`` writes a
deterministic dataset: the company's books, a manifest, and the ground truth of the
errors injected into them. ``python -m opensumma`` runs the same command.
"""

import argparse
import sys
from pathlib import Path

from opensumma.datasets import (
    BOOKS_FILE,
    DEFAULT_YEAR,
    GROUND_TRUTH_FILE,
    MANIFEST_FILE,
    write_dataset,
)


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
    args = parser.parse_args(argv)

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
        generate.error(str(error))
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

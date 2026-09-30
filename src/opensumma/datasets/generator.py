"""Generating a dataset: a company's books, what they are, and where they are wrong.

A dataset is three files in a directory:

- ``books.db``, the company's books: a SQLite database at the current schema, which
  the REST and MCP interfaces can serve as they are;
- ``manifest.json``, what the dataset is: how it was generated, its fingerprint,
  and a summary of its records and year-end position;
- ``ground_truth.json``, the answer key: every injected error, the entries as
  recorded and as they should be, and how far each account is off. An agent being
  evaluated is given the books alone.

The same company, seed, year, transaction count, and error count always give the
same books, fingerprint, and ground truth.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opensumma.datasets.books import books_fingerprint
from opensumma.datasets.business import DEFAULT_YEAR, plan_business
from opensumma.datasets.errors import (
    ERROR_TYPES,
    InjectedError,
    Ref,
    default_error_count,
    inject_errors,
)
from opensumma.datasets.model import Plan, Transaction
from opensumma.datasets.recorder import RecordedIds, record_plan
from opensumma.db import create_engine, init_db
from opensumma.kernel import (
    Account,
    JournalEntry,
    JournalLine,
    balance_sheet,
    income_statement,
    trial_balance,
)
from opensumma.objects import (
    AccountingObject,
    AccountingObjectType,
    Counterparty,
    CounterpartyKind,
)
from opensumma.workflow import audited

# Bumped whenever the same parameters would generate different books.
GENERATOR_VERSION = 1
FORMAT = 1
BOOKS_FILE = "books.db"
MANIFEST_FILE = "manifest.json"
GROUND_TRUTH_FILE = "ground_truth.json"
_COMPANY = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


@dataclass(frozen=True)
class DatasetPlan:
    """A dataset before it is recorded: its parameters, its plan with the errors
    in it, and the record of each error."""

    company: str
    seed: int
    transactions: int
    year: int
    plan: Plan
    errors: tuple[InjectedError, ...]


@dataclass(frozen=True)
class Dataset:
    """What a generated dataset is, and its answer key, as their JSON files hold
    them."""

    manifest: dict[str, Any]
    ground_truth: dict[str, Any]


def plan_dataset(
    *,
    company: str,
    transactions: int,
    seed: int,
    errors: int | None = None,
    year: int = DEFAULT_YEAR,
) -> DatasetPlan:
    """Plan a dataset without recording it, refusing parameters it cannot meet.

    ``errors`` defaults to one per hundred transactions, and at least one of each
    of the eleven types; 0 gives clean books.
    """
    if not _COMPANY.fullmatch(company):
        raise ValueError(
            "a company is named in lowercase letters, digits, and hyphens, such as "
            f"'acme', at most 40 characters; got {company!r}"
        )
    for name, value in (("transactions", transactions), ("seed", seed)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
    count = default_error_count(transactions) if errors is None else errors
    clean = plan_business(transactions=transactions, seed=seed, year=year)
    plan, injected = inject_errors(clean, seed=seed, count=count)
    return DatasetPlan(company, seed, transactions, year, plan, tuple(injected))


def generate_dataset(
    session: Session,
    *,
    company: str,
    transactions: int,
    seed: int,
    errors: int | None = None,
    year: int = DEFAULT_YEAR,
) -> Dataset:
    """Generate a dataset into the empty books ``session`` opens.

    The caller commits. The whole generation is one audited action by the system.
    """
    planned = plan_dataset(
        company=company, transactions=transactions, seed=seed, errors=errors, year=year
    )
    return record_dataset(session, planned)


def record_dataset(session: Session, planned: DatasetPlan) -> Dataset:
    """Record a planned dataset into the empty books ``session`` opens."""
    if session.scalars(select(Account.id).limit(1)).first() is not None:
        raise ValueError("a dataset is generated into empty books")
    with audited(
        session,
        actor=None,
        action="generate_dataset",
        # The seed and the error count are left out: the log is readable by the
        # agents being evaluated.
        input={
            "company": planned.company,
            "year": planned.year,
            "transactions": planned.transactions,
        },
        reason="Generated a synthetic company's books",
    ) as audit:
        ids = record_plan(session, planned.plan)
        counts = _counts(session)
        audit.output = {"counts": counts}
    year_end = _year_end(session, planned.year)
    fingerprint = books_fingerprint(session)
    parameters = {
        "company": planned.company,
        "year": planned.year,
        "seed": planned.seed,
        "transactions": planned.transactions,
    }
    manifest = {
        "format": FORMAT,
        "generator_version": GENERATOR_VERSION,
        **parameters,
        "books": BOOKS_FILE,
        "fingerprint": fingerprint,
        "counts": counts,
        "year_end": year_end,
    }
    ground_truth = {
        "format": FORMAT,
        "generator_version": GENERATOR_VERSION,
        **parameters,
        "fingerprint": fingerprint,
        "error_types": list(ERROR_TYPES),
        "errors": [
            _error_json(number, error, ids, len(planned.errors))
            for number, error in enumerate(planned.errors, start=1)
        ],
    }
    return Dataset(manifest, ground_truth)


def write_dataset(
    directory: Path | str,
    *,
    company: str,
    transactions: int,
    seed: int,
    errors: int | None = None,
    year: int = DEFAULT_YEAR,
) -> Dataset:
    """Generate a dataset into ``directory``: its books, manifest, and ground truth.

    Refuses to overwrite a dataset already there. Parameters are checked before any
    file is written.
    """
    planned = plan_dataset(
        company=company, transactions=transactions, seed=seed, errors=errors, year=year
    )
    directory = Path(directory)
    files = [
        directory / name for name in (BOOKS_FILE, MANIFEST_FILE, GROUND_TRUTH_FILE)
    ]
    for path in files:
        if path.exists():
            raise FileExistsError(
                f"{path} already exists; choose another directory or remove it"
            )
    directory.mkdir(parents=True, exist_ok=True)
    books = files[0]
    url = f"sqlite:///{books}"
    try:
        init_db(url)
        engine = create_engine(url)
        try:
            with Session(engine) as session:
                dataset = record_dataset(session, planned)
                session.commit()
        finally:
            engine.dispose()
    except BaseException:
        books.unlink(missing_ok=True)  # a partial file this call created
        raise
    _write_json(files[1], dataset.manifest)
    _write_json(files[2], dataset.ground_truth)
    return dataset


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _counts(session: Session) -> dict[str, int]:
    def count(*where: Any, model: Any) -> int:
        statement = select(func.count()).select_from(model).where(*where)
        return int(session.scalar(statement) or 0)

    session.flush()
    return {
        "accounts": count(model=Account),
        "vendors": count(
            Counterparty.kind == CounterpartyKind.VENDOR, model=Counterparty
        ),
        "customers": count(
            Counterparty.kind == CounterpartyKind.CUSTOMER, model=Counterparty
        ),
        "journal_entries": count(model=JournalEntry),
        "journal_lines": count(model=JournalLine),
        "accounting_objects": count(model=AccountingObject),
        "bank_transactions": count(
            AccountingObject.object_type == AccountingObjectType.BANK_TRANSACTION,
            model=AccountingObject,
        ),
    }


def _year_end(session: Session, year: int) -> dict[str, Any]:
    """The year-end position, read from the ledger; the books must balance."""
    end = date(year, 12, 31)
    trial = trial_balance(session, as_of=end)
    sheet = balance_sheet(session, as_of=end)
    income = income_statement(session, start=date(year, 1, 1), end=end)
    if not (trial.is_balanced and sheet.is_balanced):
        raise AssertionError("the generated books do not balance")
    return {
        "as_of": end.isoformat(),
        "trial_balance_total": str(trial.total_debits),
        "total_assets": str(sheet.assets.total),
        "total_liabilities": str(sheet.liabilities.total),
        "total_equity": str(sheet.total_equity),
        "revenue": str(income.revenue.total),
        "expenses": str(income.expenses.total),
        "net_income": str(income.net_income),
        "is_balanced": True,
    }


def _error_json(
    number: int, error: InjectedError, ids: RecordedIds, total: int
) -> dict[str, Any]:
    width = max(3, len(str(total)))
    # The objects the error concerns: those of its entries as recorded, and any
    # bank statement line it names, such as one no entry records.
    object_ids = sorted(
        {
            *(ids.documents[t.key] for t in error.actual if t.key in ids.documents),
            *(ids.bank[t.key] for t in error.actual if t.key in ids.bank),
            *(
                ids.bank[value.key]
                for value in error.details.values()
                if isinstance(value, Ref) and value.kind == "bank"
            ),
        }
    )
    return {
        "id": f"ERR-{number:0{width}d}",
        "type": error.error_type,
        "summary": error.summary,
        "period": error.as_of.isoformat()[:7],
        "journal_entry_ids": [ids.entries[t.key] for t in error.actual],
        "accounting_object_ids": object_ids,
        "actual": [_entry_json(t, ids) for t in error.actual],
        "expected": [_entry_json(t, ids) for t in error.expected],
        "misstatement": {
            "as_of": error.as_of.isoformat(),
            "accounts": {a: str(v) for a, v in error.misstatement().items()},
        },
        "details": {
            name: _resolve(value, ids) for name, value in error.details.items()
        },
    }


def _entry_json(transaction: Transaction, ids: RecordedIds) -> dict[str, Any]:
    """A journal entry as the ground truth shows it; ``id`` is None for an entry
    the books do not hold."""
    return {
        "id": ids.entries.get(transaction.key),
        "entry_date": transaction.entry_date.isoformat(),
        "description": transaction.description,
        "lines": [
            {
                "account": line.account,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "dimensions": dict(line.dimensions),
            }
            for line in transaction.lines
        ],
    }


def _resolve(value: Any, ids: RecordedIds) -> Any:
    if isinstance(value, Ref):
        found = {"entry": ids.entries, "document": ids.documents, "bank": ids.bank}
        return found[value.kind][value.key]
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_resolve(item, ids) for item in value]
    return value

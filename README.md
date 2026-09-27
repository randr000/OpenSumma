# OpenSumma

An open-source accounting environment for building and evaluating AI accounting agents.

OpenSumma provides a deterministic double-entry accounting kernel that AI agents interact
with through semantic tools and workflows. The core rule:

> **AI proposes and reasons. The accounting kernel validates and records.**

The project is under active, phased development. See [progress.md](progress.md) for the
current state and [docs/roadmap.md](docs/roadmap.md) for the plan.

## Development setup

Requires Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Common commands

```bash
pytest                   # run the test suite
ruff check .             # lint
ruff format .            # format
mypy                     # type-check
alembic upgrade head     # create/upgrade the database schema
```

The database URL is read from `OPENSUMMA_DATABASE_URL` and defaults to
`sqlite:///opensumma.db` in the current directory. The schema can also be created from
Python:

```python
from opensumma.db import init_db

init_db("sqlite:///opensumma.db")
```

## Using the kernel

The accounting kernel is usable directly from Python, with no server running. It
covers the chart of accounts, accounting periods, dimensions, journal entries, the
ledger they post to, and the financial reports derived from it.

```python
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import (
    JournalEntryError,
    LineInput,
    balance_sheet,
    create_calendar_year_periods,
    create_journal_entry,
    income_statement,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    seed_dimensions,
    trial_balance,
    validate_journal_entry,
)

url = "sqlite:///opensumma.db"
init_db(url)

with Session(create_engine(url)) as session:
    seed_chart_of_accounts(session)
    seed_dimensions(session)
    create_calendar_year_periods(session, 2026)
    session.commit()

    # Record drafts, then validate and post them.
    capital = create_journal_entry(
        session,
        entry_date=date(2026, 3, 1),
        description="Owner investment",
        lines=[
            LineInput("1111", debit=Decimal("10000.00")),
            LineInput("3100", credit=Decimal("10000.00")),
        ],
    )
    aws = create_journal_entry(
        session,
        entry_date=date(2026, 3, 15),
        description="AWS invoice for March, unpaid",
        lines=[
            LineInput(
                "6100", debit=Decimal("120.50"), dimensions={"DEPARTMENT": "ENG"}
            ),
            LineInput("2110", credit=Decimal("120.50")),
        ],
    )
    print(validate_journal_entry(session, aws))  # []
    for entry in (capital, aws):
        post_journal_entry(session, entry)
    session.commit()

    # Validation reports every problem at once, with stable codes.
    draft = create_journal_entry(
        session,
        entry_date=date(2026, 3, 15),
        description="Misposted",
        lines=[
            LineInput("6000", debit=Decimal("10.00")),  # a parent account
            LineInput("1111", credit=Decimal("9.99")),  # does not balance
        ],
    )
    try:
        post_journal_entry(session, draft)
    except JournalEntryError as error:
        print([issue.code.value for issue in error.issues])
        # ['UNBALANCED', 'ACCOUNT_NOT_POSTABLE']

    # Reports derive from the posted ledger only; the draft above is not in it.
    march_end = date(2026, 3, 31)
    trial = trial_balance(session, as_of=march_end)
    print(trial.total_debits, trial.total_credits)  # 10120.50 10120.50
    march = income_statement(session, start=date(2026, 3, 1), end=march_end)
    print(march.net_income)  # -120.50
    sheet = balance_sheet(session, as_of=march_end)
    print(sheet.assets.total, sheet.total_liabilities_and_equity)  # 10000.00 10000.00

    # A posted entry is never edited; it is reversed by a new entry.
    reversal = reverse_journal_entry(session, aws, entry_date=march_end)
    session.commit()
    print(aws.status.value, reversal.status.value)  # REVERSED POSTED
```

## Documentation

- [docs/architecture.md](docs/architecture.md): layers, boundaries, and infrastructure decisions
- [docs/accounting-model.md](docs/accounting-model.md): accounting invariants and domain model
- [docs/agent-model.md](docs/agent-model.md): how AI agents interact with the kernel
- [docs/roadmap.md](docs/roadmap.md): phases and acceptance criteria

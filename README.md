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

The accounting kernel is usable directly from Python, with no server running. Phase 1
covers the chart of accounts, accounting periods, and dimensions.

```python
from datetime import date

from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import (
    NotPostableError,
    assert_postable,
    create_calendar_year_periods,
    get_account,
    period_for_date,
    postable_accounts,
    resolve_dimension_value,
    seed_chart_of_accounts,
    seed_dimensions,
)

url = "sqlite:///opensumma.db"
init_db(url)

with Session(create_engine(url)) as session:
    seed_chart_of_accounts(session)
    seed_dimensions(session)
    create_calendar_year_periods(session, 2026)
    session.commit()

    # Only leaf accounts are postable; parents only aggregate.
    assert_postable(get_account(session, "6100"))
    try:
        assert_postable(get_account(session, "6000"))
    except NotPostableError as error:
        print(error)  # account 6000 aggregates child accounts; post to a leaf instead

    print(period_for_date(session, date(2026, 3, 15)).code)  # 2026-03
    print(resolve_dimension_value(session, "DEPARTMENT", "ENG").name)  # Engineering
    print(len(postable_accounts(session)))  # 30
```

## Documentation

- [docs/architecture.md](docs/architecture.md): layers, boundaries, and infrastructure decisions
- [docs/accounting-model.md](docs/accounting-model.md): accounting invariants and domain model
- [docs/agent-model.md](docs/agent-model.md): how AI agents interact with the kernel
- [docs/roadmap.md](docs/roadmap.md): phases and acceptance criteria

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

## Documentation

- [docs/architecture.md](docs/architecture.md): layers, boundaries, and infrastructure decisions
- [docs/accounting-model.md](docs/accounting-model.md): accounting invariants and domain model
- [docs/agent-model.md](docs/agent-model.md): how AI agents interact with the kernel
- [docs/roadmap.md](docs/roadmap.md): phases and acceptance criteria

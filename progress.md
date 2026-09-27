# Progress

## Current Phase:

Phase 0 — Repository and development infrastructure

## Current Status:

Complete. All Phase 0 acceptance criteria are satisfied. Phase 1 has not started.

## Completed:

Phase 0 acceptance criteria:

- [x] Python project initializes successfully (`pyproject.toml`, hatchling, `src/` layout,
  package `ledgerlab`, Python >= 3.12)
- [x] Package installs successfully (editable and non-editable; the built wheel contains the
  migrations)
- [x] SQLite connection works (`ledgerlab.db.create_engine`, foreign keys enforced)
- [x] Alembic works (in-package migrations, baseline revision, `alembic.ini` for the CLI,
  `init_db()` for code, upgrade/downgrade round trip, model/migration drift check)
- [x] pytest works (unit / integration / acceptance layout; warnings are errors)
- [x] Ruff works (lint and format; generated migrations are auto-formatted)
- [x] Basic CI configuration exists (`.github/workflows/ci.yml`: Python 3.12 and 3.13,
  Ruff, mypy, pytest)
- [x] docs/architecture.md exists
- [x] docs/accounting-model.md exists
- [x] progress.md exists

Also added: `docs/agent-model.md`, `docs/roadmap.md`, `README.md`, and mypy (strict).

Money storage (decided early, ahead of Phase 2): `ledgerlab.money.Money` stores amounts
as exact integer cents, never floats. `round_money()` rounds to two decimal places
(half-up) where amounts enter the system, and the column rejects anything with more than
two decimal places.

## In Progress:

Nothing.

## Next:

Phase 1 — chart of accounts, account hierarchy, account types and normal balances,
accounting periods, dimensions, and a seed chart of accounts. First, settle the Phase 1
open decisions in [docs/accounting-model.md](docs/accounting-model.md#open-design-decisions):
postable leaf accounts and the currency scope.

## Known Issues:

- CI has not run on GitHub yet, because the repository has no remote. The same commands
  pass locally on Python 3.12 and 3.13.
- There is no license file yet. The project is intended to be open source, but the license
  has not been chosen.

## Last Verification:

2026-09-27, on Python 3.12.14 and 3.13.15:

- `pytest`: 25 passed (12 unit, 12 integration, 1 acceptance)
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel, installed non-editably into a fresh environment, and the
  test suite passed against the installed package.
- SQLite databases were initialized at the migration head through `init_db()` (explicit
  and default URL) and through `alembic upgrade head`. `alembic check` detected no
  drift, and offline `--sql` mode worked.

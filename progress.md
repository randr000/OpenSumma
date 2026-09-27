# Progress

## Current Phase:

Phase 1 — Chart of accounts, dimensions, accounting periods

## Current Status:

Complete. All Phase 1 acceptance criteria are satisfied. Phase 2 has not started.

## Completed:

Phase 0 — repository and development infrastructure. Packaging (`opensumma`, hatchling,
`src/` layout, Python >= 3.12), SQLite/SQLAlchemy foundation with foreign keys enforced,
in-package Alembic migrations, pytest in unit / integration / acceptance layers with
warnings as errors, Ruff, strict mypy, and CI on Python 3.12 and 3.13.

Phase 1 acceptance criteria:

- [x] Account model exists (`opensumma.kernel.models.Account`)
- [x] Account hierarchy works (self-referential parent/child; a child shares its
  parent's account type; an account is active only if all its ancestors are)
- [x] Account types work (the five classifications, constrained in Python and by a
  database CHECK)
- [x] Normal balances work (stored per account, defaulting from the type, so contra
  accounts such as accumulated depreciation carry the opposite side)
- [x] Accounting periods work (non-overlapping date ranges, open/closed, a date resolves
  to exactly one period)
- [x] Dimensions work (department / location / class axes with a fixed set of values;
  unknown or retired values are rejected)
- [x] Seed chart of accounts exists (`opensumma.kernel.seed`: 41 accounts across all five
  types, two contra accounts, plus three dimensions)
- [x] Tests cover core invariants (112 tests: 22 unit, 82 integration, 8 acceptance)

## In Progress:

Nothing.

## Next:

Phase 2 — journal entries, journal lines, and deterministic validation. The kernel gates
Phase 2 will call already exist: `assert_postable()`, `assert_period_open()`,
`period_for_date()`, and `resolve_dimension_value()`.

One rule Phase 2 must add: an account that already carries postings must not be allowed
to gain child accounts, because that would retroactively make a posted-to account an
aggregate. Phase 1 cannot enforce this, as nothing can be posted yet.

## Known Issues:

- CI has not run on GitHub yet, because the repository has no remote. The same commands
  pass locally on Python 3.12 and 3.13.
- There is no license file yet. The project is intended to be open source, but the license
  has not been chosen.
- Accounts and periods have no delete operation, so a mistyped code can only be
  deactivated. Deletion is deliberately absent: from Phase 3 on, anything referenced by
  the ledger must survive forever, and a general delete would have to know that.

## Design decisions settled in Phase 1:

Recorded in [docs/accounting-model.md](docs/accounting-model.md#settled-decisions).

1. **One generic currency with two decimal places.** No currency column anywhere.
2. **No zero-amount lines.** Each journal line has exactly one strictly positive side
   (enforced in Phase 2, recorded as invariant 6 now).
3. **Only leaf accounts are postable.** Parents aggregate. Postability is derived from
   the hierarchy rather than stored, so there is one source of truth.
4. **UTC only.** `opensumma.utc.UtcDateTime` rejects naive timestamps and returns
   timezone-aware UTC, because SQLite silently drops `tzinfo`. Accounting dates stay
   plain `date` values.

Also decided: enum columns are portable text plus an explicit CHECK constraint, not
`sa.Enum(create_constraint=True)`, whose constraint Alembic renders a second time and so
produces duplicate constraints sharing one name — harmless on SQLite, fatal on
PostgreSQL. See [docs/architecture.md](docs/architecture.md#database).

## Last Verification:

2026-09-27, on Python 3.12.14 and 3.13.15:

- `pytest`: 112 passed (22 unit, 82 integration, 8 acceptance)
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma.kernel` and both migrations,
  installed non-editably into a fresh Python 3.13 environment, and the whole suite passed
  against the installed package.
- `alembic upgrade head` created all four tables, `alembic check` reported no drift,
  `alembic downgrade base` unwound both revisions, and offline `--sql` mode emitted the
  full schema.
- The schema created by the migration was compared statement by statement with the schema
  created from the models: every constraint appears exactly once, with the same name.

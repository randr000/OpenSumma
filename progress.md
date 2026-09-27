# Progress

## Current Phase:

Phase 2 — Journal entries and deterministic validation

## Current Status:

Complete. All Phase 2 acceptance criteria are satisfied. Phase 3 has not started.

## Completed:

Phase 0 — repository and development infrastructure. Packaging (`opensumma`, hatchling,
`src/` layout, Python >= 3.12), SQLite/SQLAlchemy foundation with foreign keys enforced,
in-package Alembic migrations, pytest in unit / integration / acceptance layers with
warnings as errors, Ruff, strict mypy, and CI on Python 3.12 and 3.13.

Phase 1 — chart of accounts, dimensions, accounting periods. A typed account hierarchy in
which only active leaves are postable, normal balances with contra accounts,
non-overlapping open/closed periods, dimensions with validated values, and a 41-account
seed chart.

Phase 2 acceptance criteria:

- [x] Journal entries exist (`JournalEntry`: accounting date, description, status,
  `posted_at`, `reversal_of`)
- [x] Journal lines exist (`JournalLine`: one account, a debit or a credit, a memo, and at
  most one value per dimension through `JournalLineDimension`)
- [x] Decimal arithmetic is used (amounts are exact `Decimal` cents end to end; the
  kernel rejects sub-cent amounts rather than rounding them)
- [x] Balanced entries validate (`validate_journal_entry` returns no issues)
- [x] Unbalanced entries fail (`UNBALANCED`, reported with both totals and the
  difference)
- [x] Invalid accounts fail (`UNKNOWN_ACCOUNT` when recording; `ACCOUNT_NOT_POSTABLE`
  and `ACCOUNT_INACTIVE` when posting)
- [x] Closed periods fail (`PERIOD_CLOSED`, and `NO_PERIOD` for dates outside every
  period)
- [x] Posted entries cannot be mutated (session hooks refuse every change to a posted,
  reversed, or voided entry and its lines, judged against the status in the database)
- [x] Reversals create new entries (a new posted entry with every side swapped; the
  original becomes REVERSED and both stay in the ledger)

CLAUDE.md's required test list is covered in full: balanced, unbalanced, single-line,
debit and credit on the same line, negative amount, inactive account, closed period,
invalid dimension, reversal, duplicate posting, and multi-line entries. Property-based
tests (Hypothesis) check that an entry posts exactly when it balances, that total posted
debits always equal total posted credits, and that reversals net every account to zero.

The rule Phase 1 deferred is in place: an account with posted lines cannot gain children
(`AccountHasPostingsError`).

## In Progress:

Nothing.

## Next:

Phase 3 — the immutable ledger and financial reports: trial balance, general ledger,
income statement, and balance sheet, all derived from posted lines. Two decisions to
settle first, recorded in
[docs/accounting-model.md](docs/accounting-model.md#open-design-decisions): whether
database triggers should back up the session hooks, and whether any dimension becomes
mandatory.

## Known Issues:

- CI has not run on GitHub yet, because the repository has no remote. The same commands
  pass locally on Python 3.12 and 3.13.
- There is no license file yet. The project is intended to be open source, but the license
  has not been chosen.
- Accounts and periods have no delete operation, so a mistyped code can only be
  deactivated. This is deliberate: anything the ledger references must survive.
- Immutability is enforced in SQLAlchemy sessions, not in the database, so raw SQL on a
  connection could still alter a posted entry. Agents never get SQL access; database
  triggers are an open Phase 3 decision.
- The kernel posts from any status that is not yet final. Restricting posting to
  APPROVED entries and to actors with POSTER permission is Phase 5's job.

## Design decisions made in Phase 2:

Recorded in [docs/accounting-model.md](docs/accounting-model.md#journal-entries-phase-2).

1. **Two tiers of rules.** Recording rejects what cannot be stored at all (negative,
   zero, or two-sided lines; sub-cent amounts; unknown accounts or dimension values).
   Posting rejects what cannot enter the ledger (too few lines, unbalanced, aggregate or
   inactive accounts, missing or closed period, retired dimension values). A draft may
   break posting rules, because it may be incomplete and master data may change before
   it is posted.
2. **Every issue at once, with stable codes.** Validation returns all problems as
   `ValidationIssue` values with an `IssueCode`, in a deterministic order, so agents can
   correct a proposal in one pass and benchmarks can compare codes exactly.
3. **Immutability below the services.** A `before_flush` hook compares every changed
   journal object against its entry's status *in the database*, because a commit
   expires objects and erases the attribute history that would otherwise reveal the
   change. The same hook stops an unbalanced entry entering the ledger by any route,
   and a `do_orm_execute` hook refuses bulk writes to journal tables.
4. **Reversal date is required.** Defaulting to the original's date fails when its
   period is closed, and defaulting to today would make results depend on when code runs.
5. **The database checks line shape.** A single CHECK makes every stored line carry
   exactly one strictly positive side. A line's dimension value is referenced together
   with its dimension through a composite foreign key, so a value cannot be filed under
   the wrong dimension.

Also: the migration creates the unique key `dimension_value (id, dimension_id)` before
the table whose foreign key targets it. Autogenerate emitted them the other way round,
which SQLite accepts and PostgreSQL rejects. The test suite dropped from 72 s to 20 s by
running integration tests on in-memory SQLite, since every DDL statement on a file waits
for the disk.

## Last Verification:

2026-09-27, on Python 3.12.14 and 3.13.15:

- `pytest`: 223 passed (50 unit, 157 integration, 16 acceptance), including 125
  generated Hypothesis scenarios
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma.kernel.journal` and all three
  migrations, installed non-editably into a fresh Python 3.13 environment, and the whole
  suite passed against the installed package.
- `alembic upgrade head` applied all three revisions, `alembic check` reported no drift,
  `alembic downgrade base` unwound all three, and offline `--sql` mode worked for SQLite
  and for PostgreSQL, where the unique key precedes the foreign key that needs it.
- The schema built by the migrations matched the schema declared by the models, table by
  table and constraint by constraint, including `dimension_value` after SQLite rebuilt it.
- The immutability tests were run with the guard removed: every test failed except the
  one confirming drafts stay editable, so each depends on the guard.
- The README example was executed and printed exactly what its comments claim.

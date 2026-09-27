# Progress

## Current Phase:

Phase 3 — Immutable ledger and financial reports

## Current Status:

Complete. All Phase 3 acceptance criteria are satisfied. Phase 4 has not started.

## Completed:

Phase 0 — repository and development infrastructure. Packaging (`opensumma`, hatchling,
`src/` layout, Python >= 3.12), SQLite/SQLAlchemy foundation with foreign keys enforced,
in-package Alembic migrations, pytest in unit / integration / acceptance layers with
warnings as errors, Ruff, strict mypy, and CI on Python 3.12 and 3.13.

Phase 1 — chart of accounts, dimensions, accounting periods. A typed account hierarchy in
which only active leaves are postable, normal balances with contra accounts,
non-overlapping open/closed periods, dimensions with validated values, and a 41-account
seed chart.

Phase 2 — journal entries and deterministic validation. Recording and posting rules
reported together with stable issue codes, posting, voiding, and reversal, and session
hooks that stop any change to a recorded entry and any unbalanced entry reaching the
ledger.

Phase 3 acceptance criteria:

- [x] Immutable ledger exists (`opensumma.kernel.ledger`: every line of every POSTED or
  REVERSED entry; immutable and append-only because posted entries are)
- [x] Trial balance works (net balances in the debit or credit column, zero balances left
  off, equal column totals)
- [x] General ledger works (lines by date, entry, and line, between an opening balance
  brought forward and a closing balance, with a running balance, memos, and dimensions)
- [x] Income statement works (revenue and expenses for a date range, rolled up the
  account hierarchy, contra revenue shown as a reduction)
- [x] Balance sheet works (assets, liabilities, and equity as of a date, contra assets
  shown as reductions, net income not yet closed carried into equity)
- [x] Accounting equation holds (`Assets = Liabilities + Equity + unclosed net income`,
  checked by `BalanceSheet.is_balanced` and by tests at several dates)
- [x] Financial reports derive from posted ledger (reports read posted data only through
  the ledger module; drafts, voided entries, and Accounting Objects never reach them)

The acceptance test posts a realistic January (capital, equipment, credit sales, a
customer payment, a sales return, rent, an unpaid bill, depreciation, payroll, and a
misposting that is reversed) alongside a draft and a voided entry, and checks every
report against figures worked out by hand. It doubles as a worked example for user
acceptance testing.

A property test builds random years of entries with random fates (posted, drafted,
voided, or posted and reversed) and checks every report against balances computed
independently from the generated data. It fails when drafts are allowed into the
ledger, which was checked by planting exactly that bug.

## In Progress:

Nothing.

## Next:

Phase 4 — the Accounting Object model and event model: business events such as vendor
bills and customer invoices as objects with flexible JSON data, linked to the journal
entries that record their accounting impact, and unable to bypass validation.

## Known Issues:

- CI has not run on GitHub yet, because the repository has no remote. The same commands
  pass locally on Python 3.12 and 3.13.
- There is no license file yet. The project is intended to be open source, but the license
  has not been chosen.
- Accounts and periods have no delete operation, so a mistyped code can only be
  deactivated. This is deliberate: anything the ledger references must survive.
- Immutability is enforced in SQLAlchemy sessions, not in the database, so raw SQL on a
  connection could still alter a posted entry. Triggers were decided against (see
  [docs/accounting-model.md](docs/accounting-model.md#open-design-decisions)).
- The kernel posts from any status that is not yet final. Restricting posting to
  APPROVED entries and to actors with POSTER permission is Phase 5's job.
- Revenue and expenses are never closed into retained earnings. The balance sheet shows
  that income as `unclosed_net_income`; year-end close belongs with period close in
  Phase 5.
- Offline SQL generation (`alembic upgrade head --sql`) does not work for SQLite past the
  Phase 2 migration, because batch mode must read the live table it rebuilds. It works
  for PostgreSQL, which is where it is useful, and a test now keeps it working there.
  **Correction:** the Phase 2 version of this file (commit `0f0de12`) claimed offline
  mode worked for SQLite. It did not; that check counted CREATE TABLE statements in the
  output without checking the command's exit code, which was non-zero.

## Design decisions made in Phase 3:

Recorded in
[docs/accounting-model.md](docs/accounting-model.md#the-ledger-and-financial-reports-phase-3).

1. **The ledger is the posted journal lines, not a copy.** Posted lines are already
   immutable and append-only, so a separate table would only add a second source of truth
   to keep in step.
2. **No database triggers.** CLAUDE.md allows database-specific SQL only where
   unavoidable, and it is avoidable: the kernel's sessions are the only writers and
   agents never get SQL access.
3. **Reports read posted data only through `ledger.py`,** so "reports derive from the
   ledger" is a property of the code, not a convention.
4. **Report dates are explicit.** Nothing defaults to today, so a report depends only on
   the ledger.
5. **Signs follow the reader's expectation.** Balances are stated in the account's normal
   direction; statement amounts in the section's, so contra accounts show as reductions.
   The trial balance uses debit and credit columns.
6. **Reports include retired accounts** that still hold posted history; omitting them
   would unbalance the reports.

Also: journal entries are indexed by accounting date, since every report selects by it.

## Last Verification:

2026-09-27, on Python 3.12.14 and 3.13.15:

- `pytest`: 271 passed (62 unit, 186 integration, 23 acceptance), including 175 generated
  Hypothesis scenarios
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `ledger.py`, `reports.py`, and all four
  migrations, installed non-editably into a fresh Python 3.13 environment, and the whole
  suite passed against the installed package.
- `alembic upgrade head` applied all four revisions, `alembic check` reported no drift,
  and `alembic downgrade base` unwound all four. Offline `--sql` generation works for
  PostgreSQL and is now tested; for SQLite it stops at the Phase 2 migration (see Known
  Issues).
- Mutation checks: with drafts and voided entries let into the ledger, both the report
  property test and the targeted report test fail; with the Phase 2 migration's
  constraint moved after the table that needs it, the new offline test fails.
- Every figure in the acceptance test was worked out by hand before the test ran, and the
  README example was executed and printed exactly what its comments claim.

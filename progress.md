# Progress

## Current Phase:

Phase 4 — Accounting Object model and event model

## Current Status:

Complete. All Phase 4 acceptance criteria are satisfied. Phase 5 has not started.

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

Phase 3 — immutable ledger and financial reports. The ledger is the posted journal
lines themselves; trial balance, general ledger, income statement, and balance sheet
all read it through `ledger.py`, and the accounting equation holds with net income
not yet closed carried into equity.

Phase 4 acceptance criteria:

- [x] AccountingObject exists (`opensumma.objects`: the specified fields, eleven object
  types, OBSERVED and VOIDED statuses; never deleted)
- [x] JSON business data supported (portable JSON column; strings, 64-bit integers,
  booleans, null, arrays, and objects round-trip exactly; floats refused on every
  write path; top-level string fields queryable)
- [x] Business events supported (`AccountingEvent`: typed, business-time ordered,
  append-only, distinct from workflow states and audit events)
- [x] Object-to-accounting-impact relationship exists (a many-to-many, permanent link
  to journal entries; `accounting_impact` derives what the ledger holds for an
  object, reversals included, through the ledger module)
- [x] Objects cannot bypass accounting validation (the kernel never imports the object
  layer; entries for objects are created and posted by the kernel; no ledger state is
  stored on objects; an object the ledger still carries cannot be voided)

The acceptance test records a realistic March: a vendor bill posted to the wrong
account, reversed, reposted, and paid; the same bill arriving twice and the duplicate
found through its business data and voided; a purchase order whose goods-received
event is accrued; an invoice and a proposal that cannot post. Every report figure was
worked out by hand beforehand, and the test shows the objects' own data claiming an
amount the ledger never recorded.

A property test writes arbitrary business data to the database and reads it back
unchanged.

## In Progress:

Nothing.

## Next:

Phase 5 — the workflow engine: states between observed and closed for objects and
journal entries, validated transitions, agent proposals entering the workflow, human
approval, and posting restricted to approved entries and to actors allowed to post.

## Known Issues:

- The repository now has a GitHub remote, and `main` was pushed through Phase 3. CI
  results have not been checked from this machine, which has no `gh` CLI. The same
  commands pass locally on Python 3.12 and 3.13.
- There is no license file yet. The project is intended to be open source, but the license
  has not been chosen.
- Accounts and periods have no delete operation, so a mistyped code can only be
  deactivated. This is deliberate: anything the ledger references must survive.
- Immutability is enforced in SQLAlchemy sessions, not in the database, so raw SQL on a
  connection could still alter a posted entry or rewrite object history. Triggers were
  decided against (see
  [docs/accounting-model.md](docs/accounting-model.md#open-design-decisions)).
- The kernel posts from any status that is not yet final. Restricting posting to
  APPROVED entries and to actors with POSTER permission is Phase 5's job.
- Revenue and expenses are never closed into retained earnings. The balance sheet shows
  that income as `unclosed_net_income`; year-end close belongs with period close in
  Phase 5.
- Offline SQL generation (`alembic upgrade head --sql`) does not work for SQLite past the
  Phase 2 migration, because batch mode must read the live table it rebuilds. It works
  for PostgreSQL, where it is useful, and a test keeps it working there.
- The kernel does not know about objects, so reversing a reversal can reinstate an entry
  that records a voided object. `accounting_impact` then shows `has_net_impact` for
  that object, which is how the contradiction is detected; nothing prevents it.
- Business data changed in place (`obj.data["amount"] = ...`) is not saved, because
  SQLAlchemy does not see changes inside a JSON value. Assign a new value instead.
- `entity_id` is an opaque identifier with no counterparty master data behind it, so an
  unknown vendor is not rejected yet (open decision 4).
- Voiding an object records no reason. Who voided it and why belongs to the audit log
  (Phase 6).
- Data filters in `search_accounting_objects` match top-level fields as text, so a
  filter of `"3"` also matches a stored integer `3`, on SQLite and PostgreSQL alike.

## Design decisions made in Phase 4:

Recorded in
[docs/accounting-model.md](docs/accounting-model.md#accounting-objects-phase-4).

1. **Objects are a layer above the kernel.** `opensumma.objects` depends on the
   kernel; the kernel never imports it, which a test enforces. The only kernel change
   is an `entry_ids` filter on `posted_activity`.
2. **`entity_id` is the counterparty, not the company.** Only objects carry it, so a
   company id would have meant nothing on accounts or entries. The dimensions follow
   NetSuite's (department, location, class), where "entity" is the customer or vendor
   on a transaction. One company per database settles open decision 1 from Phase 3.
3. **Business events are separate from workflow states and audit events.** They record
   what happened in the world, in business time, and are append-only.
4. **An object's status never claims ledger state.** OBSERVED and VOIDED say whether it
   stands; whether it is recorded is derived from its entries.
5. **Objects link to entries from their own table, many-to-many and permanently.**
   Linking changes nothing in the ledger, and an entry's reversals count toward the
   object's impact automatically.
6. **Floats are refused in business data at the column type,** so no write path can
   put one next to an accounting amount; amounts are strings.
7. **Voiding needs zero net impact and no pending entries,** so voiding an object can
   never remove anything from the ledger; a voided object is final.
8. **Shared helpers moved down a layer:** the enum column helpers into `db.py`, and
   `ensure_utc()` into `utc.py` as the single definition of an acceptable timestamp.

## Last Verification:

2026-09-28, on Python 3.12.14 and 3.13.15:

- `pytest`: 354 passed (89 unit, 236 integration, 29 acceptance), including 275
  generated Hypothesis scenarios
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/objects/` and all five
  migrations, installed non-editably into a fresh Python 3.13 environment, and the
  whole suite, Ruff, and mypy passed against the installed package.
- `alembic upgrade head` applied all five revisions, `alembic check` reported no drift,
  and `alembic downgrade base` unwound them. Offline PostgreSQL SQL now includes the
  object tables, and a test checks it.
- Mutation checks, each caught by the suite and then undone: the kernel importing the
  object layer; voiding that ignores net ledger impact; impact that ignores
  reversals; a business-data column that accepts floats; voided objects left
  unguarded in the flush hook; and the ledger's entry filter letting drafts in.
- Every figure in the acceptance test was worked out by hand before the test ran, and
  both README examples were executed and printed exactly what their comments claim.

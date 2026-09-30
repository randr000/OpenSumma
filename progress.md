# Progress

## Current Phase:

Phase 9 — deterministic dataset generator

## Current Status:

Complete. All Phase 9 acceptance criteria are satisfied. Phase 10 has not started.

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

Phase 4 — Accounting Object model and event model. Business documents as objects
with JSON business data (floats refused), append-only business events, and a
permanent many-to-many link to the journal entries that record their impact, which is
derived from the ledger. The kernel never imports the object layer.

Phase 5 — workflow / state machine. Actors with explicit permissions; state machines
for entries, objects, and periods as tables; approval with segregation of duties;
posting only once approved; ordered period close; an append-only transition history.
Counterparties replaced `entity_id`.

Phase 6 — audit / event log. Every workflow action, allowed or refused, and every
change made outside the workflow, in one hash-chained, append-only audit log with
concise reasons and evidence references.

Phase 7 — FastAPI REST interface. Every workflow operation and read over HTTP,
identified by an actor's API key, with READ_ONLY gating every read, refusals
committed so their audit events survive, and amounts as JSON strings.

Phase 8 — MCP interface. The semantic tools over MCP on stdio: 15 read-only and 14
mutating tools, kept in separate modules and annotated as such, acting as the actor
whose API key starts the server, with the REST interface's permissions, audit, and
JSON, which both interfaces now share in `opensumma.interface`.

Phase 9 acceptance criteria:

- [x] Deterministic dataset generation works (`erp dataset generate --company acme
  --transactions 1000 --seed 42`, run by the acceptance test as the installed
  command, writes `books.db`, `manifest.json`, and `ground_truth.json`)
- [x] Seed produces reproducible data (the same parameters give the same books,
  fingerprint, and byte-identical manifest and ground truth; another seed gives
  another dataset; a test pins one dataset's fingerprint and ground truth, on
  Python 3.12 and 3.13, and another shows the plan does not depend on hash
  randomization)
- [x] 1,000 transaction dataset works (about 5 seconds; the REST interface serves
  it as it is)
- [x] 10,000 transaction dataset works (about 35 seconds, 65 customers, over 5,000
  bank statement lines, 100 errors)
- [x] Generated companies balance (trial balance and balance sheet at every month
  end; cash and inventory never negative; receivables and payables equal their open
  items)
- [x] Error injection works (all eleven types in the specification, spread in turn,
  no type taking more than a quarter of its candidates)
- [x] Ground truth is preserved (every error's entries match the books, and the
  errored books differ from the clean books of the same seed by exactly the ground
  truth at every month end; a correction built from it validates in the workflow)

The generator plans a year as plain values first, a fixed schedule of 237
transactions and a variable business filling the rest exactly, injects errors into
the plan, and records it through the kernel and the object layer as one audited
action. See [docs/datasets.md](docs/datasets.md).

## In Progress:

Nothing.

## Next:

Phase 10 — the benchmark (`erp benchmark run`): a task schema and at least ten
deterministic tasks over generated datasets (GL-001 to JE-003), scored exactly
against their ground truth, with agent trajectories recorded and results exported.

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
- The kernel still posts from any status that is not yet final, and the object layer
  still records entries for objects without a counterparty. Both are deliberate: the
  workflow enforces approval, permissions, and counterparties for every actor, and the
  layers beneath stay open to trusted code such as the dataset generator, which must
  be able to create the mistakes the controls catch. Nothing stops untrusted Python
  code from calling them; the REST and MCP interfaces expose only the workflow.
- Revenue and expenses are never closed into retained earnings. The balance sheet shows
  that income as `unclosed_net_income`, which stays correct. Year-end closing entries
  were not part of Phase 5: they need entries marked as closing, so that the closed
  year's income statement still shows its income, which reaches into the reports
  (open decision 2).
- READ_ONLY is enforced by the REST and MCP interfaces but not in Python, where
  reading is not gated. Reads are not audited; agent trajectories (Phase 10) will
  record tool calls.
- Offline SQL generation (`alembic upgrade head --sql`) does not work for SQLite past the
  Phase 2 migration, because batch mode must read the live table it rebuilds. It works
  for PostgreSQL, where it is useful, and a test keeps it working there.
- The kernel does not know about objects, so reversing a reversal can reinstate an entry
  that records a voided object. `accounting_impact` then shows `has_net_impact` for
  that object, which is how the contradiction is detected; nothing prevents it.
- Business data changed in place (`obj.data["amount"] = ...`) is not saved, because
  SQLAlchemy does not see changes inside a JSON value. Assign a new value instead.
- A database migrated from Phase 4 gets one counterparty per distinct `entity_id`,
  with the kind inferred from the objects that named it (a customer only if every
  such object was a customer document). Converted counterparties are worth reviewing.
- Operations on the kernel or object layer called directly, outside the workflow,
  leave no workflow history; they are in the audit log, as the system's changes.
- Audit events are part of the caller's unit of work. The REST interface commits after
  a refusal, so its event survives; Python callers that roll back discard it.
- A REST request or MCP call that does not match the schema, or names a record that
  does not exist (an unknown entry id, an unknown object in a proposal), is refused
  before it reaches the workflow and so is not audited. Over MCP, a schema mismatch
  is reported by the SDK as text naming each field, not as structured JSON.
- `/ledger` and `/accounting-objects`, and the `get_ledger` and
  `search_accounting_objects` tools, return everything matching their filters, with
  no paging; the audit history pages. A 10,000-transaction dataset has over 22,000
  ledger lines and 15,000 objects, so an unfiltered read is large; the benchmark
  (Phase 10) will want paging.
- API keys do not expire; revoking them, or deactivating the actor, cuts access. The
  server has no TLS or rate limiting: it is meant for a local laboratory.
- `search_transactions`, `get_open_ap`, and `get_open_ar` have no endpoint or tool
  yet; the last two wait on settlement between payments and bills, which generated
  datasets record only in business data (each payment names its invoice number),
  and the first has not been defined. Nothing lists journal entries by status either, so an
  approving agent finds entries awaiting approval through the audit history
  (`get_audit_history` with `action="submit_for_approval"`). Phase 11's agents will
  want both.
- The MCP server runs on stdio only, one actor per server process, so a workflow
  with several actors runs several servers. An HTTP transport would need
  per-request identity, which the SDK provides only as OAuth.
- Refusing unknown MCP arguments replaces each tool's argument model after the SDK
  builds it (`Tool.fn_metadata.arg_model` and `Tool.parameters`). The SDK documents
  those fields as read on every call, but they are not a declared extension point,
  so an SDK upgrade could break it; the tests that invent arguments would catch
  that.
- The project requires the MCP SDK 2.x (`mcp>=2.2`); version 1's `FastMCP` API is
  gone. The SDK brings its own dependencies, among them `cryptography`, `pyjwt`,
  `jsonschema`, and `sse-starlette`.
- Starlette 1.x prefers `httpx2` for its test client and warns about `httpx`, which
  the suite treats as an error, so the dev dependency is `httpx2`.
- Raw SQL is beyond the audit log, as it is beyond the immutability guards: a change
  made that way to a record other than an audit event leaves no event. Changes to the
  audit log itself are detected by `verify_audit_log`, except removing events from
  the very end; keeping the head hash it returns elsewhere covers that.
- Audit events are numbered with a unique sequence. On PostgreSQL, two transactions
  auditing at once would both claim the next number and one would fail; Phase 12
  should serialize this.
- Capturing every change costs time: the suite runs about a third slower, since
  seeding a chart of accounts now writes an event per row. Trusted batch code can
  record itself as one action with `audited` instead.
- Data filters in `search_accounting_objects` match top-level fields as text, so a
  filter of `"3"` also matches a stored integer `3`, on SQLite and PostgreSQL alike.
- Datasets cover one calendar year, with every period open and nothing closed into
  retained earnings. The bank statement covers the operating account only; the
  payroll account, funded exactly and emptied each payday, has none.
- Generated records have no workflow history: the generator records beneath the
  workflow, as trusted code, and sets its documents' statuses (`CLASSIFIED`, or
  `EXTRACTED` for unmatched statement lines and bills without a vendor) directly.
- `--transactions` counts the clean books' entries. Duplicates and unusual charges
  add entries and missing accruals and unreconciled payments leave them out, so
  the errored books hold a few more or fewer; the manifest gives the count.
- How hard each error is to find has not been measured. Normal card expenses fall
  on business days only, so an unusual charge's weekday alone gives it away, and
  a duplicate bill repeats its original's description exactly.
- A change to the generator that changes its output fails the test pinning one
  dataset's fingerprint and ground truth; it must bump `GENERATOR_VERSION` and
  update the pinned values.
- Generation was measured up to 10,000 transactions (about 35 seconds). It records
  row by row through the kernel, so larger datasets take proportionally longer, and
  the acceptance suite now takes about a minute more.

## Design decisions made in Phase 9:

Recorded in [docs/datasets.md](docs/datasets.md) and
[docs/architecture.md](docs/architecture.md#datasets).

1. **Plan, then record.** A year is planned as plain values with no database, and
   errors injected into the plan, before anything is written. Bad parameters are
   refused before a file exists, and the business can be tested in milliseconds.
2. **Exactly N transactions,** each one journal entry in the clean books: a fixed
   schedule of 237, the same for every company, and a variable business sized to
   fill the rest.
3. **Errors from their own random stream,** so a seed's clean and errored books
   share the same business, and the ground truth is exactly their difference: the
   entries as recorded and as they should be, which the tests check at every month
   end.
4. **Only `random.random()`,** in a stream per purpose, since Python guarantees its
   sequence but not the algorithms of `randint`, `choice`, or `shuffle`. Datasets
   are reproducible in content, shown by a fingerprint of the books that leaves out
   when rows were written, rather than byte for byte.
5. **Costs sized to gross profit.** Sales are planned first and payroll, rent, and
   the opening balances follow from what they earn, so every size and customer mix
   gives a plausible margin, and cash and inventory never go negative.
6. **Trusted, beneath the workflow, as one audited action.** The workflow would
   refuse the errors the generator must create, so it records through the kernel,
   whose validation and guards still apply to every entry. Autoflush is suspended
   and flushes batched, which halves the time.
7. **The bank statement as objects.** Every operating-account movement has a
   `bank_transaction` line; bank charges, payroll transfers, and tax payments are
   recorded from theirs, and the rest wait, unmatched, for reconciliation.
8. **Three files, and the agent gets one.** The books, a manifest, and the ground
   truth. The audit log records the generation without the seed or the error count,
   since agents can read it.
9. **An `erp` console script** built on `argparse`, like the other entry points,
   with `python -m opensumma` beside it; Phase 10 adds `erp benchmark run`.

## Last Verification:

2026-09-29, on Python 3.12.14 and 3.13.15:

- `pytest`: 713 passed (220 unit, 432 integration, 61 acceptance), including the
  acceptance tests that run the installed `erp` command and generate a
  10,000-transaction dataset
- `ruff check .`: passed
- `ruff format --check .`: passed. It formats the README's Python examples too, and
  one line of the MCP example added in Phase 8 was too long, so the Phase 8 commit
  did not pass this check; it is fixed here.
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/datasets/`, `opensumma/cli.py`,
  the `erp` console script, and all eight migrations; installed non-editably into a
  fresh Python 3.13 environment, the whole suite, Ruff, and mypy passed against it.
  An unanchored `datasets/` in `.gitignore`, meant for generated output, had left
  the generator's own package out of the wheel; it is anchored to the root now.
- The golden dataset test gives the same fingerprint and ground truth on Python
  3.12 and 3.13, and the planner gives the same plan under different
  `PYTHONHASHSEED` values.
- Mutation checks, each caught by the suite and then undone: draws taken from
  `randint`; a plan iterating a set; a plan one transaction short; ground truth
  misstating the correct entry; a missing accrual whose reversal stays in the
  books; unusual charges on weekdays; entries recorded but not posted; bank
  statement lines not recorded; the seed written to the audit log; a fingerprint
  including posting times; an existing dataset overwritten; and the generator
  importing the REST interface.
- The README's Python examples were executed in order, the dataset example
  included, and every printed value matched.

# Progress

## Current Phase:

Signed journal amounts — a change of representation after Phase 12

## Current Status:

Complete. Journal lines carry one signed amount instead of separate debit and credit
columns, throughout the kernel, the interfaces, the datasets, the benchmark, and the
example agents. Every phase's acceptance tests still pass, on SQLite and PostgreSQL.

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

Phase 9 — deterministic dataset generator. `erp dataset generate` writes a
company's year of books (exactly N transactions, planned from a seed, recorded
through the kernel), a manifest with a content fingerprint, and the ground truth
of eleven types of injected error, which is exactly how the books differ from the
clean books of the same seed. See [docs/datasets.md](docs/datasets.md).

Phase 10 — benchmark / evaluation framework. `erp benchmark run` runs an agent on
sixteen tasks, each on its own copy of a dataset's books, through the MCP tools
served in-process, and scores every answer exactly against the books and the ground
truth; it writes the eight metrics of the specification, per-task results, and
every tool call as a trajectory. An oracle runs each task's reference solution as a
calibration. See [docs/benchmark.md](docs/benchmark.md).

Phase 11 — example accounting agents. An investigation agent, a journal entry agent,
and a duplicate-invoice agent (`opensumma.agents`), which apply explicit accounting
rules through the MCP tools alone, audit every change with its reason and evidence,
and together score 1.0 on all sixteen benchmark tasks of the standard dataset. See
[docs/agents.md](docs/agents.md).

Phase 12 — PostgreSQL compatibility. psycopg 3 through the `postgresql` extra; the
migrations, kernel, objects, workflow, audit log, REST, and MCP behave the same on
PostgreSQL, where the whole suite runs when `OPENSUMMA_TEST_POSTGRESQL_URL` names a
server; the same seed gives the same books on both backends; concurrent writers keep
the books consistent; and CI runs the suite on PostgreSQL. See
[docs/architecture.md](docs/architecture.md#database) and
[#concurrency](docs/architecture.md#concurrency).

Signed journal amounts (after Phase 12, on request). Journal lines carried a
non-negative debit and a non-negative credit, exactly one of them above zero; they now
carry one signed `amount`, the algebraic sign convention: a debit is positive, a
credit negative, and a line may be zero. Criteria set for the change:

- [x] One `amount` column per journal line (signed integer cents), with a migration,
  `db546c4d06cf`, that converts every existing line as `debit - credit` in SQL; its
  downgrade splits them back, and refuses books holding a zero line, which the
  earlier schema cannot represent
- [x] An entry balances when its amounts sum to zero (`JournalEntry.total`); a
  reversal negates every amount; the rules that a single signed amount makes
  impossible to break, and the rule against zero lines, are gone with their issue
  codes (`NEGATIVE_AMOUNT`, `DEBIT_AND_CREDIT`, `ZERO_AMOUNT`)
- [x] Zero-amount lines are recorded, posted, and reported, and change no balance
- [x] Balances are signed like the lines: account balances, the general ledger's
  running balances, and a one-column trial balance whose total is zero; only the
  income statement and balance sheet state amounts in each section's normal direction
- [x] REST and MCP take and return signed amounts, and refuse a line written with the
  former `debit` and `credit` fields; the tools' descriptions state the convention
- [x] Datasets (generator version 2, format 2), their ground truth, the benchmark
  (version 2), and the example agents use signed amounts. The books generated for a
  seed hold the same entries, amounts, and errors as before, which was checked
  against the previous generator; only their form, and so the fingerprint, changed.
  Every task's score for the oracle is unchanged
- [x] Every phase's acceptance tests pass, on SQLite and on PostgreSQL

See [docs/accounting-model.md](docs/accounting-model.md#signed-amounts).

## In Progress:

Nothing.

## Next:

The phase order is complete. Candidates, none begun: a model-driven agent measured
against the example agents (through the same `Agent` protocol); year-end closing
entries (open decision 2 in the accounting model); `search_transactions`,
`get_open_ap`, and `get_open_ar`; paging for the ledger and object reads; and a
license.

## Known Issues:

- The repository now has a GitHub remote, and `main` was pushed through Phase 3. CI
  results have not been checked from this machine, which has no `gh` CLI. The same
  commands pass locally on Python 3.12 and 3.13. The PostgreSQL job (Phase 12) has
  never run on GitHub: it was verified only by running its steps locally, against
  a local PostgreSQL 16 in place of the service container.
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
  (`get_audit_history` with `action="submit_for_approval"`). The example agents did
  without them: the investigation agent works out open invoices and settlements
  from the documents' business data, and no example agent approves.
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
- Transactions that audit append to the log one after another (Phase 12): on
  PostgreSQL each holds an advisory lock from its first audit event until it
  commits, so write transactions serialize from that point. That bounds write
  throughput, which suits a laboratory; a hash chain is serial by nature.
- SQLite books are for one writer at a time. SQLite has no row locks, so the locks
  that keep concurrent PostgreSQL transactions consistent are not sent to it, and
  several processes writing one SQLite file at once are not protected: a race there
  can fail on the unique audit sequence, or let a second approval of one entry
  through. PostgreSQL is the backend for concurrent use.
- On PostgreSQL, two transactions that lock rows in opposite orders can deadlock;
  PostgreSQL then rolls one back and the caller gets the error, with no retry.
  Workflow operations lock one subject each, so it takes an unusual mix of
  operations in one transaction.
- The PostgreSQL work was verified locally on PostgreSQL 16.2, from the binaries of
  the `pgserver` Python package (no system PostgreSQL or running Docker here), and CI
  uses the `postgres:16` image; other versions are untested. The test fixtures drop
  databases `WITH (FORCE)`, which needs PostgreSQL 13 or later.
- The suite takes about ten minutes on PostgreSQL, against six on SQLite: each test
  creates a database from a template and drops it afterwards.
- `erp dataset generate` and the benchmark work on SQLite files only, since a dataset
  is a file handed to an agent and copied per task. A company is generated into
  PostgreSQL with `generate_dataset` from Python
  ([docs/datasets.md](docs/datasets.md#on-postgresql)).
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
- The injected errors are easy to find once one knows what evidence to read: the
  example agents find every one with a single rule per type (Phase 11, see
  [docs/agents.md](docs/agents.md#how-they-score)). Normal card expenses also fall on
  business days only, so an unusual charge's weekday alone gives it away (the
  agents do not use it), and a duplicate bill repeats its original's description
  exactly.
- A change to the generator that changes its output fails the test pinning one
  dataset's fingerprint and ground truth; it must bump `GENERATOR_VERSION` and
  update the pinned values.
- Generation was measured up to 10,000 transactions (about 35 seconds). It records
  row by row through the kernel, so larger datasets take proportionally longer, and
  the acceptance suite now takes about a minute more.
- The oracle is a calibration, not a baseline: for the tasks that find errors it
  answers from the ground truth without calling a tool. The example agents are the
  baselines, and they too score full marks on the standard dataset, so the benchmark
  does not yet tell a good agent from a perfect one on these errors; what it can
  measure is how far a model-driven agent falls short of a few rules. No
  model-driven agent exists yet; one plugs in through the same `Agent` protocol.
- Tasks are worded in English, and an agent may depend on the wording (the tests'
  scripted agents read ids and dates from it). Changing a task's wording, choices,
  expected answer, or scoring fails the test pinning the oracle's results on one
  dataset; it must bump `BENCHMARK_VERSION` and update the pinned value.
- AR-001 expects the invoices the recorded payments leave unpaid, so the invoice of
  a payment the books never recorded (AR-002's error) counts as outstanding.
- Every task grants the agent READ_ONLY and PROPOSER. The schema carries
  permissions per task, but no task yet asks an agent to approve or post.
- Tasks run one after another, with no time limit: an agent that hangs hangs the
  run. Each task copies the books (about 1.2 MB for the standard dataset), and
  trajectories keep every tool result in full, so an agent that reads the whole
  ledger leaves a large trajectory. The example agents read the whole ledger, or
  every object, for most of the tasks that find errors: about 6 MB of trajectories
  on the standard dataset, and GL-005's alone over 1 MB.
- Trajectories keep each tool result as the agent saw it, including when records
  were written (`created_at`, `updated_at`, `posted_at`). For the records a task
  sets up during the run, such as the clerk's bill in JE-001 and JE-003, that is the
  time of the run, so those fields differ from run to run; Phase 10's claim that
  nothing in a trajectory does was wrong, since the oracle, the only agent it was
  tested with, never reads those records. Results are unaffected and stay
  byte-identical. The fields are kept rather than masked, so a trajectory is always
  what the agent saw.
- The example agents' known shortfalls: two deposits of the same amount on the
  same day, neither naming its payer, one of them unrecorded, cannot be told apart
  (AR-002 scored 0.8 once in sixteen datasets); a vendor with no posted bill gives
  the journal entry agent nothing to learn from, and it declines to guess, which
  happens in companies of about 300 transactions, where JE-001 and JE-003 can pick
  such a vendor, a task no precedent can answer; the unusual-charge rule judges
  card charges only, since one vendor's bills differ by twenty times in ordinary
  books; and each agent chooses its workflow by task id and reads ids and dates
  from the instructions, so a change of wording can break it.
- The journal entry agent follows the way most of the vendor's ten latest posted
  bills were recorded. Were most of those misposted, it would follow them, and say
  so in its evidence.
- Findings' reasons and evidence are recorded in `results.json` but not scored:
  nothing yet measures whether an agent's explanation is right, only its answer.
- Datasets generated before signed amounts (generator version 1, format 1) cannot be
  used: their books keep debits and credits apart, and the benchmark refuses them
  with a message to generate them again, which gives the same entries for the same
  parameters. `alembic upgrade head` would convert their books, but not their ground
  truth, whose fingerprint would then no longer match. Benchmark version 2 words
  GL-001 and GL-002 for signed balances and expects GL-002's answer, and the journal
  entry tasks' lines, in the signed form, so an agent answering in the former form
  scores less than before; the oracle's scores are unchanged.
- An agent or script written for the former interface, which sends `debit` and
  `credit` fields, is refused with a schema error and must send one signed `amount`
  per line.
- Audit events recorded before the change keep the form they were recorded in: their
  inputs name `debit` and `credit`, and refusals may carry the retired issue codes
  (`NEGATIVE_AMOUNT`, `DEBIT_AND_CREDIT`, `ZERO_AMOUNT`). The log is append-only and
  hash-chained, so they are not converted.
- Books holding a zero-amount line cannot be downgraded below revision
  `db546c4d06cf`: the earlier schema has no way to store such a line, and the
  downgrade refuses rather than altering posted lines.
- An entry made only of zero lines balances, and so posts, although it records
  nothing; no rule refuses it.

## Design decisions made for signed amounts:

Recorded in [docs/accounting-model.md](docs/accounting-model.md#signed-amounts) and
[#balances-and-signs](docs/accounting-model.md#balances-and-signs). Phase 12's are in
[docs/architecture.md](docs/architecture.md#database) and
[#concurrency](docs/architecture.md#concurrency).

1. **One signed column, debits positive.** The algebraic sign convention: an entry
   balances when its lines sum to zero, a reversal negates each amount, and an
   account's balance is the sum of its amounts. The rules a single signed amount
   cannot break, a negative debit or credit and a line that is both, went with their
   issue codes rather than surviving as codes nothing can raise; so did the rule
   against zero lines, which are now allowed.
2. **Balances signed like the lines.** Account balances, the general ledger's
   running balances, and the trial balance carry the lines' sign, so a balance is
   the sum of the amounts an agent reads in the ledger, and the trial balance is one
   column that totals zero. Each balance still reports its account's normal balance.
3. **Statements in their sections' direction.** The income statement and balance
   sheet keep stating revenue, liabilities, and equity as positive, as statements
   are read; they had no debit and credit columns to merge, and their sections
   already fix the side.
4. **No derived debit and credit totals.** The entry's, trial balance's, and account
   balance's separate debit and credit totals are gone rather than kept as derived
   views, so nothing presents two columns. An entry's `total` is the sum of its
   amounts, zero when it balances; the UNBALANCED message says which side is larger
   and by how much.
5. **Never -0.00.** Negating a zero amount gives `Decimal("-0.00")`, which would
   print as such and change fingerprints. `ensure_money` returns `0.00` for it, and
   the kernel negates by subtracting from zero.
6. **Old datasets are generated again, not migrated.** The generator, the dataset
   format, and the benchmark each took a new version; a dataset in the former format
   is refused when loaded rather than failing in the middle of a run. Generated books
   were compared with the previous generator's, entry by entry, before the pinned
   fingerprints and results were updated.
7. **A data migration in SQL, with no lossy downgrade.** `amount = debit - credit`
   in one statement, so offline SQL generation includes it. The downgrade splits each
   amount by its sign and refuses books holding a zero line instead of deleting or
   inventing a side for it.

## Last Verification:

2026-10-02, on Python 3.12.14 and PostgreSQL 16.2, after the change to signed amounts:

- `pytest` on SQLite: 816 passed and 12 skipped, the tests that need a PostgreSQL
  server (828 in all: 274 unit, 473 integration, 81 acceptance)
- `pytest` on PostgreSQL, with `OPENSUMMA_TEST_POSTGRESQL_URL` naming a local server:
  828 passed
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- Python 3.13 was not run this time; nothing in the change depends on the version.
- The migration was run on books holding a posted entry with a dimension value on a
  line, on SQLite and PostgreSQL: its lines became `debit - credit`, the dimension
  survived the table's rebuild, `alembic check` found the schema equal to the
  models, the downgrade restored the debits and credits, and books holding a zero
  line were refused (all now in `test_migrations.py`).
- Generated datasets were compared with the previous generator's: a
  1,000-transaction company (seed 42) and the pinned 300-transaction one (seed 2026)
  hold the same entries and lines, `amount` equal to the former `debit - credit` on
  all 2,467 lines of the first, the same year-end figures, and the same ground truth
  once its former lines are read as signed amounts. Only the fingerprints changed.
- The oracle's benchmark results on the pinned dataset, before and after: the same
  metrics and the same score on every task. They differ in the benchmark version, the
  dataset fingerprint, GL-001's and GL-002's instructions, GL-002's answer, and the
  journal entry tasks' expected lines.
- The README's Python examples were executed in order on SQLite, and every printed
  value matched.

# Progress

## Current Phase:

Phase 10 — benchmark / evaluation framework

## Current Status:

Complete. All Phase 10 acceptance criteria are satisfied. Phase 11 has not started.

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

Phase 10 acceptance criteria:

- [x] Benchmark task schema exists (`Task`: id, title, category, what it measures,
  the agent's permissions, a Pydantic answer model that gives its JSON schema, and
  deterministic `prepare`, `score`, and reference `solve`; `erp benchmark tasks
  --json` exports it)
- [x] At least 10 benchmark tasks exist (sixteen: the specification's eleven
  examples, GL-001 to JE-003, and GL-005, GL-006, AP-003, AP-004, and AR-002)
- [x] Deterministic scoring exists (expected answers from the books and the ground
  truth; F1 for sets of items, exact amounts, journal entries line by line and by
  their place in the workflow; the oracle scores 1.0 on every task, the null agent
  0; the same agent gets byte-identical results)
- [x] Agent trajectories can be recorded (every tool call, with its arguments and
  result or refusal, in `trajectories/<task>.jsonl`, and the answer)
- [x] Benchmark CLI works (`erp benchmark run`, run by the acceptance test as the
  installed command in an empty directory, generates the standard dataset and runs
  the oracle; `--agent module:attribute` runs any agent)
- [x] Results can be exported (`results.json` with the eight metrics of the
  specification, `results.csv` with one row per task, and each task's books with the
  agent's audit log)

Agents act only through the MCP tools, served in-process as the actor `agent`, on
a copy of the books per task. See [docs/benchmark.md](docs/benchmark.md).

## In Progress:

Nothing.

## Next:

Phase 11 — example accounting agents: an investigation agent, a journal entry
agent, and a duplicate-invoice agent, working through the tools rather than SQL,
their actions audited, and evaluated by the benchmark.

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
- The oracle is a calibration, not a baseline: for the tasks that find errors it
  answers from the ground truth without calling a tool. There is no real agent to
  compare it with until Phase 11, and how hard each task is has not been measured.
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
  ledger leaves a large trajectory.

## Design decisions made in Phase 10:

Recorded in [docs/benchmark.md](docs/benchmark.md) and
[docs/architecture.md](docs/architecture.md#benchmark).

1. **Agents use the MCP tools, in-process.** The runner serves the real MCP server
   for each task and gives the agent a synchronous `Tools` over it, so agents are
   measured on the interface any MCP client has, with its schemas, permissions,
   refusals, and audit, and need no transport or asynchronous code.
2. **One workspace per task,** a copy of the books with the agent and a clerk
   registered, so tasks are independent, the dataset is never touched, and the
   agent's changes and audit log remain for inspection.
3. **Expected answers from the data, never from a model.** From the books for
   balances and open invoices; from the ground truth for errors; from what the task
   set up for journal entries. Scores are F1 for sets of items, exact for amounts,
   and line by line for entries, with the workflow scored apart.
4. **The metrics come from records, not the agent's account of itself:** the
   answer, the trajectory, and the workspace's audit log.
5. **A task carries its reference solution.** The oracle runs it, which calibrates
   the scoring and shows every task is solvable through the tools. It is the only
   agent given the answer key.
6. **Answers as Pydantic models,** which validate the answer and give its JSON
   schema, for an LLM agent's tool definition. Fields a task does not ask for are
   ignored; amounts are strings, as everywhere else.
7. **Reproducible results:** no timestamps in results or trajectories, and task
   choices drawn from a stream seeded by the dataset's seed and the task's id.
8. **An agent is any object with `name` and `run`,** loaded by `--agent
   module:attribute`, so Phase 11's agents, scripted or model-driven, plug in
   without changes to the benchmark.

## Last Verification:

2026-09-29, on Python 3.12.14 and 3.13.15:

- `pytest`: 769 passed (245 unit, 457 integration, 67 acceptance), including the
  acceptance tests that run `erp benchmark run` as the installed command in an
  empty directory, and a custom agent loaded from its own module
- `ruff check .`: passed
- `ruff format --check .`: passed, the README's examples included
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/benchmark/`, the dataset
  generator, and all eight migrations; installed non-editably into a fresh Python
  3.13 environment, the whole suite, Ruff, and mypy passed against it.
- The oracle's results on the pinned dataset hash the same on Python 3.12 and 3.13.
- Mutation checks, each caught by the suite and then undone: an empty answer
  agreeing with any expected answer; calls left out of the trajectory; rejected
  calls counted as refusals; the agent served the dataset's own books; agents
  granted approval by default; every named record taken to exist; changes counted
  as documented without evidence; results stamped with the time of the run; an
  entry scored though it does not record the bill; answers refusing fields a task
  does not ask for; a correction scored without its original voided; and the
  benchmark importing the REST interface.
- The README's Python examples were executed in order, the benchmark example
  included, and every printed value matched.

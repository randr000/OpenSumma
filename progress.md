# Progress

## Current Phase:

Phase 11 — example accounting agents

## Current Status:

Complete. All Phase 11 acceptance criteria are satisfied. Phase 12 has not started.

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

Phase 11 acceptance criteria:

- [x] Example investigation agent (`InvestigationAgent`, `--agent investigator`:
  balances, the trial balance, and open invoices from the books, and eight kinds of
  error found by one accounting rule each; it only reads)
- [x] Example JE agent (`JournalEntryAgent`, `--agent journal-entry`: records a bill
  as the vendor's latest posted bills were recorded, validates, and submits only a
  valid entry; validates a colleague's entry; voids an invalid one, citing its
  issue codes, and records its bill afresh)
- [x] Example duplicate-invoice agent (`DuplicateInvoiceAgent`, `--agent
  duplicate-invoice`: bills from one vendor with the same invoice number, compared
  as letters and digits, and payments for a bill already paid in full)
- [x] Agents interact through tools rather than SQL (the agents may import only
  the benchmark's `TaskPrompt`, `Tools`, and `ToolCall`, one another, and six
  computing modules of the standard library, which the layering test enforces;
  in every workspace the readers leave the books' fingerprint unchanged, the SYSTEM
  records nothing but the setup, and every audit event by the agent is one of its
  tool calls, in order)
- [x] Agent actions are auditable (every change is a workflow tool call by the
  AGENT `agent` with a concise reason and evidence references, such as the precedent
  entries a proposal follows, in a verified hash chain; every read and change is in
  the trajectory; every finding is reported beside the answer with its reason and
  evidence)
- [x] Benchmark can evaluate agents (`erp benchmark run --agent examples`, or any of
  the three, run by the acceptance test as the installed command in an empty
  directory; on the standard dataset the three together score 1.0 on all sixteen
  tasks, with every metric at its best, and each alone 1.0 on its own tasks; the
  same agent gets byte-identical results)

The agents use no language model: they are deterministic baselines, and examples of
an agent built on the tools. See [docs/agents.md](docs/agents.md).

## In Progress:

Nothing.

## Next:

Phase 12 — PostgreSQL compatibility: the same schema, migrations, and suite on
PostgreSQL, with audit sequence numbers serialized between concurrent transactions.

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

## Design decisions made in Phase 11:

Recorded in [docs/agents.md](docs/agents.md) and
[docs/architecture.md](docs/architecture.md#example-agents).

1. **Rules, not a model.** The example agents apply explicit accounting rules, so
   they are deterministic, testable in CI, and baselines a model-driven agent can be
   measured against; a model-driven agent needs nothing new from the benchmark, since
   it plugs in through the same `Agent` protocol.
2. **The tools are the only way to the books.** The agents are a package above the
   benchmark that may import only its `TaskPrompt`, `Tools`, and `ToolCall`, one
   another, and six computing modules of the standard library. Allowing the whole
   standard library would allow `sqlite3`, files, and sockets.
3. **Learned from the books, not from the generator.** How to record a vendor's
   bill comes from its posted bills; which account a category belongs to, from what
   most of its documents agree on; which account the bank statement is for, from
   the entries that record its lines. Nothing reads `opensumma.datasets`, where the
   generator's own vendor accounts are.
4. **Rules written from the documented evidence, then checked against ground
   truth on other datasets.** Each rule reads the evidence
   [docs/datasets.md](docs/datasets.md) lists for its error. The unusual-charge rule
   was revised twice when checked against sixteen other datasets: the largest other
   charge hid several unusual charges at one merchant, and vendor bills vary too
   much to judge by amount.
5. **Findings explained beside the answer.** Reads are not audited, so a read-only
   agent's reasons and evidence go in an unscored `findings` field of its answer,
   which results and trajectories keep.
6. **Never guess, never overreach.** With no precedent, or a refused call it needs,
   an agent stops and says why. The journal entry agent validates before it
   submits, submits only a valid entry, and never approves or posts.
7. **The benchmark names the example agents, but does not import them,** since they
   are built on it: `--agent examples` resolves to `opensumma.agents:ExampleAgents`.
8. **Trajectories stay faithful.** When the agents showed that trajectories hold the
   times records were written during the run, the documentation was corrected rather
   than the trajectories masked.

## Last Verification:

2026-09-30, on Python 3.12.14 and 3.13.15:

- `pytest`: 807 passed (267 unit, 467 integration, 73 acceptance), including the
  acceptance tests that run each example agent with `erp benchmark run` as the
  installed command in an empty directory
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/agents/` and all eight
  migrations; installed non-editably into a fresh Python 3.13 environment, the whole
  suite, Ruff, and mypy passed against it.
- `erp benchmark run --agent examples`, in an empty directory as the README shows,
  generated the standard dataset and scored 1.0 on all sixteen tasks, every metric
  at its best, in about 20 seconds.
- The example agents were run through the benchmark on sixteen more datasets (300
  to 2,000 transactions, seeds 1 to 2026, up to one error per fifteen transactions,
  and the standard dataset without errors): no false alarm on clean books, and
  every injected error found, but for the shortfalls under Known Issues.
- Mutation checks, each caught by the suite and then undone: an agent importing
  `sqlite3`; unusual charges judged against the largest other charge; receipts
  matched to deposits the bank received before them; an entry submitted without
  being validated; a proposal without evidence; the journal entry agent trying to
  approve its own entry; and findings left out of the answer.

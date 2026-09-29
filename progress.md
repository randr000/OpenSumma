# Progress

## Current Phase:

Phase 8 — MCP interface

## Current Status:

Complete. All Phase 8 acceptance criteria are satisfied. Phase 9 has not started.

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

Phase 8 acceptance criteria:

- [x] MCP server starts (`python -m opensumma.mcp`, on stdio, as the actor whose key
  is in `OPENSUMMA_API_KEY`; the acceptance test launches it as a subprocess and
  speaks MCP to it with the SDK's client, and checks it will not start without a
  key)
- [x] Read tools work (15 read-only tools: the chart, accounts, balances, periods,
  dimensions, counterparties, journal entries, the ledger, all four reports,
  accounting objects, and the audit history, each requiring READ_ONLY)
- [x] Proposal tools work (`propose_journal_entry`, amounts as strings, refused with
  issue codes when the kernel cannot record it; and `observe_accounting_object`,
  `extract_accounting_object`, and `classify_accounting_object`)
- [x] Validation tool works (`validate_journal_entry`)
- [x] Permission checks work (every mutating tool is the workflow operation of its
  name, with its permission, controls, and audit; an agent that tries to approve
  or post is refused, and the attempt is in the audit log)
- [x] MCP integration tests pass

The MCP and REST interfaces now share `opensumma.interface`: the response models,
the views, and the unit of work, moved out of `opensumma.api` so that neither
interface depends on the other.

## In Progress:

Nothing.

## Next:

Phase 9 — the deterministic dataset generator (`erp dataset generate`): companies
with a chart, counterparties, and a year of realistic transactions from a seed, the
same seed giving the same books, with errors injected and their ground truth kept.

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
  no paging; the audit history pages. A large dataset (Phase 9) will want paging on
  the others.
- API keys do not expire; revoking them, or deactivating the actor, cuts access. The
  server has no TLS or rate limiting: it is meant for a local laboratory.
- `search_transactions`, `get_open_ap`, and `get_open_ar` have no endpoint or tool
  yet; the last two wait on settlement between payments and bills, and the first
  has not been defined. Nothing lists journal entries by status either, so an
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

## Design decisions made in Phase 8:

Recorded in [docs/architecture.md](docs/architecture.md#mcp-interface) and
[docs/agent-model.md](docs/agent-model.md#tools).

1. **A shared package, not a dependency between interfaces.** The response models,
   views, and unit of work moved from `opensumma.api` to `opensumma.interface`, so
   the MCP server never imports FastAPI and an entry or refusal looks the same on
   either interface. The layering test keeps the two interfaces peers.
2. **One actor per server, its key from the environment,** checked on every call,
   so revocation takes effect at once. The key stays out of the process list.
3. **Every tool is listed to every actor.** Permissions refuse, and the refusal is
   audited, rather than hiding tools: an agent's attempt to approve its own work is
   exactly what a benchmark needs to see. Read-only and mutating tools are kept
   apart in their own modules and by the `readOnlyHint` annotation.
4. **Refusals are error results carrying the audit log's JSON,** so an agent reads
   the error, issue codes, and missing permission as structured data.
5. **Unknown arguments are refused,** as the REST interface refuses unknown fields;
   the SDK would otherwise ignore them silently.
6. **One JSON document per result,** with lists in named fields, because the SDK
   sends a bare list as one text block per item and an empty one as nothing.
7. **The tools mirror the REST interface,** plus a `data` filter on
   `search_accounting_objects` for finding duplicate documents, and a required
   `reason` in the schema of the four tools whose operation requires one.
   `search_transactions` is left until the benchmark says what it must find.

## Last Verification:

2026-09-29, on Python 3.12.14 and 3.13.15:

- `pytest`: 629 passed (169 unit, 408 integration, 52 acceptance), including the
  acceptance test that launches `python -m opensumma.mcp` and speaks MCP to it over
  stdio
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/mcp/`, `opensumma/interface/`,
  and all eight migrations, and requiring `mcp>=2.2`; installed non-editably into a
  fresh Python 3.13 environment, the whole suite, Ruff, and mypy passed against it.
- Mutation checks, each caught by the suite and then undone: refusals rolled back
  and their audit events lost; reads without READ_ONLY; the key authenticated once
  instead of on every call; unknown arguments ignored; refusals answered as plain
  text; every tool annotated read-only; a proposal bypassing the workflow; the MCP
  interface importing the REST interface; the shared views importing the MCP SDK;
  and the server starting without a key.
- The README's Python examples were executed in order, the MCP example included,
  and every printed value matched.

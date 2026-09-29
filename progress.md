# Progress

## Current Phase:

Phase 7 — FastAPI REST interface

## Current Status:

Complete. All Phase 7 acceptance criteria are satisfied. Phase 8 has not started.

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

Phase 7 acceptance criteria:

- [x] FastAPI application starts (`python -m opensumma.api`; the acceptance test starts
  it as a real server process and queries it over HTTP; `/health` reports whether
  the schema is current)
- [x] Read endpoints work (accounts, balances, periods, dimensions, counterparties,
  journal entries, the ledger, accounting objects, and the audit log, all requiring
  READ_ONLY)
- [x] Journal proposal endpoint works (`POST /journal-entries`, amounts as strings,
  refused with issue codes when the kernel cannot record it)
- [x] Validation endpoint works (`POST /journal-entries/{id}/validate`)
- [x] Approval endpoint works (`POST /journal-entries/{id}/approve`, with submit and
  reject beside it)
- [x] Posting endpoint works (`POST /journal-entries/{id}/post`, and reverse)
- [x] Reports are accessible (trial balance, income statement, balance sheet,
  general ledger)
- [x] API integration tests pass

Callers identify themselves with an actor's API key; the workflow's permissions,
controls, and audit apply to every change made through the API. A refused action is
committed, so its audit event survives, as recommended at the end of Phase 6.

## In Progress:

Nothing.

## Next:

Phase 8 — the MCP interface: the semantic tools as an MCP server, read-only and
mutating tools kept apart, with the same identity, permissions, and audit as the
REST interface.

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
  be able to create the mistakes the controls catch. Nothing yet stops untrusted code
  from calling them; the REST and MCP interfaces (Phases 7 and 8) will expose only
  the workflow.
- Revenue and expenses are never closed into retained earnings. The balance sheet shows
  that income as `unclosed_net_income`, which stays correct. Year-end closing entries
  were not part of Phase 5: they need entries marked as closing, so that the closed
  year's income statement still shows its income, which reaches into the reports
  (open decision 2).
- READ_ONLY is enforced by the REST interface but not in Python, where reading is not
  gated. Reads are not audited; agent trajectories (Phase 10) will record tool calls.
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
- A REST request that does not match the schema, or names a record that does not
  exist (an unknown entry id in the path, an unknown object in a proposal), is
  refused before it reaches the workflow and so is not audited.
- `/ledger` and `/accounting-objects` return everything matching their filters, with
  no paging; `/audit-events` pages. A large dataset (Phase 9) will want paging on the
  first two.
- API keys do not expire; revoking them, or deactivating the actor, cuts access. The
  server has no TLS or rate limiting: it is meant for a local laboratory.
- `search_transactions`, `get_open_ap`, and `get_open_ar` have no endpoint yet; the
  last two wait on settlement between payments and bills.
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

## Design decisions made in Phase 7:

Recorded in [docs/architecture.md](docs/architecture.md#rest-interface) and
[docs/agent-model.md](docs/agent-model.md#tools).

1. **A thin adapter.** `opensumma.api` holds no accounting logic: reads call the
   kernel and object layer, and every change is a workflow operation. No domain layer
   imports it or any web framework, which the layering test enforces.
2. **API keys, not claimed identities.** A header naming an actor would let an agent
   claim to be its approver. Keys are random, shown once, stored as SHA-256 hashes in
   the workflow layer, and revocable.
3. **READ_ONLY gates every read,** so the permission defined in Phase 5 now means
   something.
4. **Refusals are committed,** so their audit events survive, and answered with the
   same JSON the audit log records, so an agent reads the same vocabulary as an
   auditor. HTTP statuses follow the kind of refusal: 401, 403, 404, 409, 422.
5. **Amounts are JSON strings;** a JSON number is refused as an amount, and requests
   refuse unknown fields, so neither a float nor a hallucinated field gets in.
6. **Beyond the specification's endpoint list:** submit, reject, and void for
   entries; observe, extract, classify, and void for objects; period close and
   reopen; and reads for periods, dimensions, counterparties, and the general
   ledger. Without submit, nothing could ever be approved.
7. **Accounts are addressed by code** (`/accounts/6100`), as agents refer to them.
8. **`/health` checks the schema revision** and answers 503 when it is not current;
   the app never migrates a database by itself.

## Last Verification:

2026-09-29, on Python 3.12.14 and 3.13.15:

- `pytest`: 586 passed (166 unit, 374 integration, 46 acceptance), including 455
  generated Hypothesis scenarios
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/api/` and all eight migrations,
  installed non-editably into a fresh Python 3.13 environment, and the whole suite,
  Ruff, and mypy passed against the installed package, including the acceptance test
  that starts the server as a process.
- `alembic upgrade head` applied all eight revisions, `alembic check` reported no
  drift, and `alembic downgrade base` unwound them. Offline PostgreSQL SQL includes
  the API key table.
- Mutation checks, each caught by the suite and then undone: refusals rolled back
  and their audit events lost; reads without READ_ONLY; revoked keys accepted; JSON
  numbers accepted as amounts; unknown request fields ignored; permission refusals
  answered with the wrong status; proposals bypassing the workflow; and the kernel
  importing FastAPI.
- The README's examples were executed, and its `curl` commands were run against a
  live server started with `python -m opensumma.api`; every response matched.

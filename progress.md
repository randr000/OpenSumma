# Progress

## Current Phase:

Phase 6 — Audit / event log

## Current Status:

Complete. All Phase 6 acceptance criteria are satisfied. Phase 7 has not started.

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

Phase 6 acceptance criteria:

- [x] Audit events exist (`AuditEvent`: timestamp, actor type and id, action, object
  type and id, input, output, result, evidence, and a concise reason)
- [x] Human/agent/system actors are distinguishable (`actor_type` on every event; a
  registered actor's id; only the SYSTEM may act without one, which the database
  enforces)
- [x] Accounting mutations create audit events (every workflow operation records one,
  allowed or refused; every change made outside the workflow is captured as the
  system's; bulk writes that would escape capture are refused)
- [x] Audit records cannot be silently modified (the ORM refuses changes and deletes;
  events are hash-chained, and `verify_audit_log` names the first event altered,
  removed, or inserted by any means)
- [x] Evidence references can be stored (a list of up to 50 references per event,
  stored and read back exactly, as in the specification's example)

The acceptance test records the specification's own example: an agent proposing with
that reason and evidence, refused when it tries to post unapproved, a human
approving, a posting service posting, and trusted code opening the books; then
someone edits the log behind the ORM and the chain exposes it. A property test builds
random logs and alters every field of every event in turn with raw SQL; each
alteration must be reported at exactly that event.

## In Progress:

Nothing.

## Next:

Phase 7 — the FastAPI REST interface: read endpoints, journal proposal, validation,
approval, and posting endpoints, and reports, as thin adapters over the workflow and
the kernel, with API integration tests.

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
- READ_ONLY is defined but not enforced, because reading from Python is not gated. The
  REST and MCP interfaces will enforce it. Reads are not audited either; the
  interfaces may record tool calls for agent trajectories (Phase 10).
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
- Audit events are part of the caller's unit of work. A refused action changes
  nothing else, so committing afterwards persists its event, but a caller that rolls
  back the whole transaction discards it too. The interfaces (Phases 7 and 8) should
  commit after a refusal.
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

## Design decisions made in Phase 6:

Recorded in [docs/agent-model.md](docs/agent-model.md#audit).

1. **One audit log, two sources.** Workflow operations record one event per call,
   named like the agent tool; changes made outside any audited action are captured
   as the system's, named `create_`, `update_`, or `delete_` and the table. Together
   they cover every accounting change, whichever path made it.
2. **Refused attempts are audited**, with the error and what it names, because what an
   agent tried and was stopped from doing is what the benchmark's invalid-posting
   and tool-use measures need.
3. **Capture listens to what the flush writes** (mapper events), not to what the
   session lists beforehand, so rows deleted as orphans by a cascade are captured.
   An early version listened to the session and missed them; a test caught it.
4. **Previous values come from the database,** read on the flush's connection,
   because SQLAlchemy forgets them once a commit has expired an object.
5. **Changes inside an action are not captured twice,** and changes pending before an
   action are flushed and captured first, so none is credited to the wrong action.
6. **Tamper evidence is a hash chain.** Each event's SHA-256 covers its content and
   the previous hash, computed over canonical JSON, so it does not depend on the
   database. This is what "cannot be silently modified" asks for beyond the ORM.
7. **A reason is capped at 500 characters and evidence is references,** which keeps
   chain-of-thought out of the log by construction.
8. **The audit log lives in the workflow package,** because its events name actors
   and the workflow writes them; a separate package would depend on the workflow
   and be depended on by it.
9. **Bulk writes are refused on every table,** since they would escape capture.

## Last Verification:

2026-09-29, on Python 3.12.14 and 3.13.15:

- `pytest`: 527 passed (165 unit, 323 integration, 39 acceptance), including 455
  generated Hypothesis scenarios and 5 fixed workflow examples
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/workflow/audit.py` and all seven
  migrations, installed non-editably into a fresh Python 3.13 environment, and the
  whole suite, Ruff, and mypy passed against the installed package.
- `alembic upgrade head` applied all seven revisions, `alembic check` reported no
  drift, and `alembic downgrade base` unwound them. Offline PostgreSQL SQL includes
  the audit table and its subject index, and a test checks it.
- Mutation checks, each caught by the suite and then undone: refusals not recorded;
  inserts not captured; deletes, including orphans, not captured; changes inside an
  action captured twice; pending changes credited to the action; audit events
  editable; bulk writes allowed; a string accepted as evidence; sequence gaps not
  checked; the hash leaving out the previous event; and the hash leaving out each of
  eight fields in turn. Two of these survived at first: the tamper property test
  picked fields at random and could miss one, so it now alters every field of every
  event; and no test forged an event and relinked its successor, which one now does.
- Every figure in the acceptance tests was worked out beforehand, and all four README
  examples were executed and printed exactly what their comments claim.

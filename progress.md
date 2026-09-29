# Progress

## Current Phase:

Phase 5 — Workflow / state machine

## Current Status:

Complete. All Phase 5 acceptance criteria are satisfied. Phase 6 has not started.

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

Phase 5 acceptance criteria:

- [x] Workflow states exist (journal entries: DRAFT through REVERSED and VOIDED;
  objects: OBSERVED, EXTRACTED, CLASSIFIED, VOIDED; periods: OPEN, CLOSED; each state
  machine a table in `opensumma.workflow.machine`)
- [x] State transitions are validated (an action is refused from any state its table
  row does not list, naming the states it is allowed from; refused steps change
  nothing)
- [x] Agent proposals can enter workflow (an AGENT actor observes, extracts, and
  classifies a bill, then proposes, validates, and submits its entry; every step
  records the agent)
- [x] Human approval can be represented (approvals and rejections are transitions
  naming a HUMAN actor and a reason; no one approves an entry they prepared)
- [x] Posting requires appropriate state/permission (only from APPROVED, only by a
  POSTER, still through the kernel's posting rules; periods close only by ADMIN, in
  order, and not over unposted entries)

Also in Phase 5, at the user's request: `entity_id` is replaced by `counterparty_id`,
referring to vendors and customers as master data, with a fixed default cast of six
vendors and five customers. A migration converts existing `entity_id` values.

The acceptance test follows four actors (an AP agent, a controller, a posting service,
and a CFO) through a month: an emailed bill from observation to posting, and an
invoice rejected for its revenue account, corrected, approved, and posted. A property
test throws random sequences of actions by random actors at an entry and checks that
the history stays one consistent chain, approvals come from non-preparers, nothing
posts unapproved, and the ledger balances.

## In Progress:

Nothing.

## Next:

Phase 6 — the audit log: an audit event for every meaningful action, human, agent,
or system, including refused attempts, with inputs, outputs, result, and evidence,
and records that cannot be silently modified.

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
  REST and MCP interfaces will enforce it.
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
- The workflow history records state changes, with their actor and reason, but not
  refused attempts, inputs, or evidence; the audit log (Phase 6) will.
- Operations on the kernel or object layer called directly, outside the workflow,
  leave no workflow history.
- Data filters in `search_accounting_objects` match top-level fields as text, so a
  filter of `"3"` also matches a stored integer `3`, on SQLite and PostgreSQL alike.

## Design decisions made in Phase 5:

Recorded in
[docs/accounting-model.md](docs/accounting-model.md#the-workflow-phase-5) and
[docs/agent-model.md](docs/agent-model.md).

1. **The workflow is a layer above the kernel and the object layer.** It wraps their
   operations under the agent tools' names; neither depends on it, which a test
   enforces. The kernel's posting semantics are unchanged.
2. **The one kernel change locks an entry's content from submission onward,** so what
   an approver approves is exactly what is posted. A rejection unlocks it.
3. **Actors are stored, with explicitly granted permissions, none implying another.**
   ADMIN closes and reopens periods and is not a superuser. Granting permissions is
   trusted setup, not a workflow action.
4. **The state machines are data:** one table per subject of actions, source states,
   target, and permission. Checks run in a fixed order: state, permission, reason,
   controls, content.
5. **Segregation of duties:** whoever proposed or submitted an entry never approves it.
6. **Validation gates submission and is repeated at approval,** since master data may
   change in between; "Validated" in the AI-native flow is that gate, not a status.
7. **Periods close in order and reopen in reverse, and never close over unposted
   entries,** so a closed period's reports never change. This settles the
   open decision on sequential close.
8. **Counterparties replace `entity_id`** as master data in the object layer, with
   the kind set by the object type. Classifying an object means settling its
   counterparty, and the workflow requires one before an entry is proposed for a
   vendor or customer document. The default cast is fixed, not random, so anything
   built on it is reproducible.
9. **The transition history is append-only** and records only state changes; the
   audit log (Phase 6) is the broader record of actions.

## Last Verification:

2026-09-29, on Python 3.12.14 and 3.13.15:

- `pytest`: 478 passed (147 unit, 297 integration, 34 acceptance), including 425
  generated Hypothesis scenarios and 5 fixed workflow examples
- `ruff check .`: passed
- `ruff format --check .`: passed
- `mypy` (strict): passed
- The package built as a wheel containing `opensumma/workflow/` and all six
  migrations, installed non-editably into a fresh Python 3.13 environment, and the
  whole suite, Ruff, and mypy passed against the installed package. That run found a
  test that left SQLite connections open, which Python 3.13 reports and the suite
  treats as an error; it now closes them.
- `alembic upgrade head` applied all six revisions and `alembic check` reported no
  drift. A Phase 4 database holding objects with `entity_id` values was upgraded: each
  value became a counterparty of the inferred kind, objects pointed at it, and the
  rebuilt table kept its CHECK constraints. Downgrading restored every `entity_id`.
  Offline PostgreSQL SQL includes the conversion and the widened status column. Tests
  now check all of this.
- Mutation checks, each caught by the suite and then undone: no segregation of
  duties; posting allowed while pending; permissions unchecked; content not locked
  under review; periods closing or reopening out of order; closing over pending
  entries; counterparty kinds unchecked; editable history; submission without
  validation; approval without revalidation; proposing or classifying a bill without
  a vendor. The workflow property test alone catches the first two; it missed
  segregation of duties at first, because random search rarely lines up a
  self-approval, so the telling sequences are now fixed examples.
- Every figure in the acceptance tests was worked out by hand beforehand, and all
  three README examples were executed and printed exactly what their comments claim.

# Agent Model

**Status:** actors, permissions, the workflow operations behind the mutating tools,
and the audit log are implemented (Phases 5 and 6, `opensumma.workflow`), and so are
the kernel and object operations behind the read-only tools (Phases 2 to 4). Every
tool is reachable over the REST interface (Phase 7, `opensumma.api`); the MCP
interface is Phase 8, the benchmark Phase 10, and example agents Phase 11.

## Principle

AI agents propose and reason; the deterministic kernel validates and records. Agents
reach the system only through semantic tools. Every mutation goes through the same
validation, whether it comes from a human, an agent, or the system.

## Actors

Every action in the workflow is taken by an `Actor`: a person (`HUMAN`), an AI agent
(`AGENT`), or a process of the system itself (`SYSTEM`). Actors are stored with the
permissions granted to them, so a transition names who took it and an agent cannot
claim to be someone else. Registering actors and setting their permissions is trusted
setup, like seeding a chart of accounts; no workflow action grants permissions. An
inactive actor cannot act.

## Permissions

Each permission is granted explicitly and none implies another: ADMIN is not a
superuser, and an approver who should also post needs POSTER too.

| Permission | Allows |
| --- | --- |
| READ_ONLY | Querying accounts, balances, ledgers, reports, objects, and history. Enforced by the interfaces (REST since Phase 7), because reading from Python is not gated |
| PROPOSER | Observing, extracting, and classifying objects; proposing, validating, and submitting entries; voiding drafts and proposals |
| APPROVER | Approving or rejecting pending entries; voiding entries under review or approved; voiding objects |
| POSTER | Posting approved entries and reversing posted ones |
| ADMIN | Closing and reopening accounting periods |

A normal AI accounting agent holds `READ_ONLY + PROPOSER`. It prepares work and
cannot approve or post it; "explicit authorization" means granting it APPROVER or
POSTER. Even then it never approves an entry it prepared: an entry's approver must not
have proposed or submitted it.

## Workflow

```text
Observed → Extracted → Classified → Proposed → Validated
    → Pending Approval → Approved → Posted → Reconciled → Closed
```

The first three stages belong to the Accounting Object, the next five to its journal
entry, and Closed to the accounting period; Reconciled is not implemented yet. The
states, transitions, controls, and history are described in
[accounting-model.md](accounting-model.md#the-workflow-phase-5). Agents generally work
in the early stages, and every step they take is recorded with them as its actor.

## Tools

Read-only and mutating tools are kept separate. Each mutating tool is a workflow
operation of the same name, which checks the state, the permission, and the
workflow's controls before the kernel checks the content.

Read-only (backed by the kernel and the object layer):

```text
get_chart_of_accounts   get_account          get_account_balance
get_trial_balance       get_general_ledger   search_transactions
get_journal_entry       get_open_ap          get_open_ar
get_accounting_object   search_accounting_objects
get_audit_history
```

`get_open_ap` and `get_open_ar` wait on settlement between payments and bills;
`get_audit_history` is backed by `audit_history`.

Mutating (the permission each requires):

| Tool | Workflow operation | Permission |
| --- | --- | --- |
| `propose_journal_entry` | `propose_journal_entry` | PROPOSER |
| `validate_journal_entry` | `validate_journal_entry` (changes nothing) | PROPOSER |
| `submit_for_approval` | `submit_for_approval` | PROPOSER |
| `approve_journal_entry` | `approve_journal_entry` | APPROVER |
| `post_journal_entry` | `post_journal_entry` | POSTER |
| `reverse_journal_entry` | `reverse_journal_entry` | POSTER |

The workflow also has operations that the tool list in the specification does not
name yet: `reject_journal_entry` and `void_journal_entry` for entries;
`observe_accounting_object`, `extract_accounting_object`,
`classify_accounting_object`, and `void_accounting_object` for objects; and
`close_period` and `reopen_period`.

Over REST (Phase 7), each tool is an endpoint, called with the actor's API key as
`Authorization: Bearer <key>`:

| Tool | Endpoint |
| --- | --- |
| `get_chart_of_accounts` | `GET /accounts` |
| `get_account` | `GET /accounts/{code}` |
| `get_account_balance` | `GET /accounts/{code}/balance?as_of=` |
| `get_trial_balance` | `GET /reports/trial-balance?as_of=` |
| `get_general_ledger` | `GET /reports/general-ledger?start=&end=&account=`, or `GET /ledger` for raw ledger lines |
| `get_journal_entry` | `GET /journal-entries/{id}` |
| `get_accounting_object` | `GET /accounting-objects/{id}` |
| `search_accounting_objects` | `GET /accounting-objects?object_type=&counterparty=&...` |
| `get_audit_history` | `GET /audit-events?object_type=&object_id=&actor=&after=&limit=` |
| `propose_journal_entry` | `POST /journal-entries` |
| `validate_journal_entry` | `POST /journal-entries/{id}/validate` |
| `submit_for_approval` | `POST /journal-entries/{id}/submit` |
| `approve_journal_entry` | `POST /journal-entries/{id}/approve` |
| `post_journal_entry` | `POST /journal-entries/{id}/post` |
| `reverse_journal_entry` | `POST /journal-entries/{id}/reverse` |

The workflow's other operations are endpoints too: `reject` and `void` for entries;
`POST /accounting-objects` (observe) and `extract`, `classify`, and `void` for
objects; `close` and `reopen` for `/periods/{code}`. Reference data an agent needs to
propose well is readable at `/periods`, `/dimensions`, and `/counterparties`, and
income statements and balance sheets at `/reports/income-statement` and
`/reports/balance-sheet`. `search_transactions`, `get_open_ap`, and `get_open_ar`
have no endpoint yet.

A refused step raises a specific error: `InvalidTransitionError` (not from this
state, naming the states it is allowed from), `PermissionDeniedError` (naming the
permission), `SegregationOfDutiesError`, `CounterpartyRequiredError`,
`PeriodSequenceError` or `PendingEntriesError` (naming what to deal with first), or
the kernel's `JournalEntryError` with its issue codes. The REST interface answers
with the same JSON the audit log records for the refusal.

## Validation results

`validate_journal_entry` and a rejected `propose_journal_entry` return every problem
at once, each as an issue with a stable code, a message, and a line number where one
applies. The codes are listed in
[accounting-model.md](accounting-model.md#two-tiers-of-rules). Agents act on codes, not
on message text, and benchmarks compare them exactly: JE-002 ("validate a journal
entry") is scored by whether an agent reports the expected codes.

## Audit

Implemented in `opensumma.workflow.audit`. Every meaningful action creates an audit
event with the fields the specification lists: `occurred_at` (the timestamp),
`actor_type`, `actor_id`, `action`, `object_type`, `object_id`, `input`, `output`,
`result`, and `evidence`, plus a concise `reason`.

Audit events never contain a model's private chain-of-thought. They hold a concise
reason, at most 500 characters, and references to the supporting evidence: at most
50, each at most 200 characters, such as `vendor_id=42`. Prose belongs in the reason;
evidence names what supports it. The specification's own example is recorded as it
reads:

```json
{
  "action": "propose_journal_entry",
  "actor_type": "AGENT",
  "reason": "Historical AWS transactions were classified to account 6100.",
  "evidence": ["vendor_id=42", "historical_account=6100"]
}
```

### What is recorded

- **Every workflow operation**, whether it SUCCEEDED or was REFUSED. The action is
  named like the agent tool (`propose_journal_entry`, `approve_journal_entry`), the
  object is the entry, object, or period it concerns, `input` holds the arguments
  and `output` what came of it. A refusal records the error, its message, and what
  it names: issue codes, the missing permission, the states an action is allowed
  from, the entries or periods to deal with first. Refusals matter for evaluation:
  an invalid posting an agent attempted is in the log even though nothing changed.
- **Every change made outside the workflow**, such as by trusted code calling the
  kernel, is captured as an event by the SYSTEM with no actor: `create_journal_entry`,
  `update_account`, `delete_journal_line`, with the values before and after. This
  catches rows deleted as orphans by a cascade too, because it listens to what the
  flush actually writes. Bulk writes, which would escape it, are refused on every
  table.
- **A batch run by trusted code** can be recorded as one action instead of row by
  row, by running it inside `audited(actor=None, action="generate_dataset", ...)`.

Changes made inside an audited action are covered by that action's event and are not
captured again. Changes pending before it are captured on their own first, so they
are never credited to the action.

`actor_type` tells a person (HUMAN), an AI agent (AGENT), and a process (SYSTEM)
apart. A registered actor is named by `actor_id`; only the SYSTEM may act without
one, which the database enforces. Audit events are part of the caller's unit of
work: committing persists them, and a refused action changes nothing else, so its
event is all a commit adds.

### Tamper evidence

Audit events are never changed or deleted through the ORM, and bulk writes to them
are refused. Beyond the ORM, events are numbered from 1 and hash-chained: each stores
the SHA-256 hash of its own content together with the hash of the event before it.
`verify_audit_log` recomputes the chain and names the first event that was changed,
removed, or inserted out of turn. Rewriting an event and relinking its successor does
not help a forger, because the successor's hash covers the link; they would have to
rewrite every later event. Removing events from the very end cannot be seen from the
chain alone, so `verify_audit_log` returns the head hash for keeping elsewhere.

`audit_history` reads the log, filtered by subject, actor, action, or result.

## Evaluation

Benchmarks score agents deterministically against known ground truth, such as account
IDs, debit/credit amounts, report values, and state transitions. An LLM judge is used
only when exact ground truth isn't available.

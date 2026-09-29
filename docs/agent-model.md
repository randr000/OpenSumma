# Agent Model

**Status:** actors, permissions, and the workflow operations behind the mutating
tools are implemented (Phase 5, `opensumma.workflow`), and so are the kernel and
object operations behind the read-only tools (Phases 2 to 4). The tools themselves
arrive with the REST and MCP interfaces (Phases 7 and 8); the audit log is Phase 6,
the benchmark Phase 10, and example agents Phase 11.

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
| READ_ONLY | Querying accounts, balances, ledgers, reports, objects, and history. Enforced by the REST and MCP interfaces, because reading from Python is not gated |
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

`get_open_ap` and `get_open_ar` wait on settlement between payments and bills, and
`get_audit_history` on the audit log.

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

A refused step raises a specific error: `InvalidTransitionError` (not from this
state, naming the states it is allowed from), `PermissionDeniedError` (naming the
permission), `SegregationOfDutiesError`, `CounterpartyRequiredError`,
`PeriodSequenceError` or `PendingEntriesError` (naming what to deal with first), or
the kernel's `JournalEntryError` with its issue codes.

## Validation results

`validate_journal_entry` and a rejected `propose_journal_entry` return every problem
at once, each as an issue with a stable code, a message, and a line number where one
applies. The codes are listed in
[accounting-model.md](accounting-model.md#two-tiers-of-rules). Agents act on codes, not
on message text, and benchmarks compare them exactly: JE-002 ("validate a journal
entry") is scored by whether an agent reports the expected codes.

## Audit

Every meaningful action records an audit event with these fields: `timestamp`,
`actor_type`, `actor_id`, `action`, `object_type`, `object_id`, `input`, `output`,
`result`, and `evidence`.

The actor types are `HUMAN`, `AGENT`, and `SYSTEM`.

Audit events never contain a model's private chain-of-thought. They hold a concise reason
and references to the supporting evidence:

```json
{
  "action": "propose_journal_entry",
  "actor_type": "AGENT",
  "reason": "Historical AWS transactions were classified to account 6100.",
  "evidence": ["vendor_id=42", "historical_account=6100"]
}
```

## Evaluation

Benchmarks score agents deterministically against known ground truth, such as account
IDs, debit/credit amounts, report values, and state transitions. An LLM judge is used
only when exact ground truth isn't available.

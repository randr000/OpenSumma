# Agent Model

**Status:** specification. Implemented across Phases 5 (workflow), 6 (audit),
7–8 (REST, MCP), 10 (benchmark), and 11 (example agents). The kernel operations the
journal tools will call exist since Phase 2; the permissions around them do not yet.

## Principle

AI agents propose and reason; the deterministic kernel validates and records. Agents
reach the system only through semantic tools. Every mutation goes through the same
validation, whether it comes from a human, an agent, or the system.

## Permissions

| Permission | Allows |
| --- | --- |
| READ_ONLY | Query accounts, balances, ledgers, reports, objects, audit history |
| PROPOSER | Create and validate proposals, submit them for approval |
| APPROVER | Approve or reject pending proposals |
| POSTER | Post approved entries and reverse posted entries |
| ADMIN | Administrative operations (defined in Phase 5) |

A normal AI accounting agent starts as `READ_ONLY + PROPOSER`. It does not approve or
post unless it has been explicitly granted that permission.

## Workflow

```text
Observed → Extracted → Classified → Proposed → Validated
    → Pending Approval → Approved → Posted → Reconciled → Closed
```

Not every workflow uses every state. Agents generally operate in the early states, and
sensitive mutations require human approval unless explicitly authorized.

## Tools

Read-only and mutating tools are kept separate.

Read-only:

```text
get_chart_of_accounts   get_account          get_account_balance
get_trial_balance       get_general_ledger   search_transactions
get_journal_entry       get_open_ap          get_open_ar
get_accounting_object   search_accounting_objects
get_audit_history
```

Mutating (the permission each requires):

```text
propose_journal_entry    PROPOSER
validate_journal_entry   PROPOSER
submit_for_approval      PROPOSER
approve_journal_entry    APPROVER
post_journal_entry       POSTER
reverse_journal_entry    POSTER
```

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

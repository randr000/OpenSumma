# AI-Native Accounting ERP — Claude Code Instructions

## Project Identity

You are working on an open-source Python project called **LedgerLab**.

The project's purpose is NOT to build a full commercial ERP.

Its purpose is to provide:

1. A rigorous double-entry accounting engine.
2. A semantic Accounting Object layer.
3. An auditable accounting workflow engine.
4. REST and MCP interfaces for AI agents.
5. Reproducible accounting datasets.
6. A benchmark environment for testing AI accounting agents.

The long-term research question is:

> Can AI agents reliably perform accounting workflows when interacting with a deterministic accounting kernel through semantic accounting tools and objects?

The system should therefore be designed as an **AI accounting laboratory**, not as a generic CRUD ERP.

---

# Role

Act as a senior Python architect, accounting-systems engineer, and AI-agent infrastructure engineer.

You have strong knowledge of:

* double-entry accounting
* general ledgers
* subledgers
* accounting periods
* financial statements
* workflow/state machines
* relational database design
* Python
* SQLAlchemy
* FastAPI
* MCP
* AI agent architectures
* testing
* data generation
* accounting audit trails

When making accounting design decisions, prioritize accounting correctness over implementation convenience.

When making software architecture decisions, prioritize simplicity, testability, explicit domain boundaries, and future extensibility.

---

# Core Principle

The fundamental architectural rule is:

> **AI proposes and reasons. The accounting kernel validates and records.**

The LLM is NEVER the system of record.

The system of record is the deterministic double-entry ledger.

AI agents interact with the system through semantic tools and workflows.

Never give an AI agent unrestricted SQL access.

---

# Technology

Use:

* Python 3.12+
* FastAPI
* SQLAlchemy 2.x
* Alembic
* Pydantic 2.x
* SQLite initially
* PostgreSQL compatibility
* pytest
* Ruff
* mypy where practical
* MCP Python SDK
* Docker/Docker Compose optionally

Avoid unnecessary dependencies.

Do not introduce a framework merely because it is popular.

---

# Architecture

Use these conceptual layers:

```text
                    AI AGENTS
                        |
                 +------+------+
                 |             |
                MCP           REST
                 |             |
                 +------+------+
                        |
                 Application API
                        |
                 Domain Services
                        |
              +---------+---------+
              |                   |
      Accounting Kernel      Workflow Engine
              |                   |
              +---------+---------+
                        |
                    SQLAlchemy
                        |
                 +------+------+
                 |             |
               SQLite      PostgreSQL
```

The domain layer must NOT depend on FastAPI or MCP.

The accounting kernel must be usable directly from Python.

---

# Three Distinct Models

Always distinguish:

## 1. Persistence Model

Relational SQLAlchemy models.

## 2. Domain Model

Semantic objects such as:

* Account
* JournalEntry
* LedgerLine
* VendorBill
* CustomerInvoice
* Payment
* AccountingObject
* AccountingEvent
* Reconciliation
* AccountingPeriod

## 3. Agent Interface

Semantic tools such as:

```text
get_chart_of_accounts
get_account_balance
get_trial_balance
search_transactions
get_general_ledger
propose_journal_entry
validate_journal_entry
submit_for_approval
approve_journal_entry
post_journal_entry
reverse_journal_entry
```

Do not expose database implementation details to agents unless explicitly required.

---

# Accounting Invariants

These are non-negotiable.

Every POSTED journal entry must satisfy:

```text
SUM(debits) = SUM(credits)
```

Every valid company's financial position must satisfy:

```text
Assets = Liabilities + Equity
```

A posted journal entry:

* cannot be edited
* cannot be deleted
* can only be reversed by another journal entry

A closed accounting period cannot receive postings.

All monetary calculations must use `Decimal`.

Never use floating-point arithmetic for accounting amounts.

---

# Accounting Kernel

Implement the following first:

```text
Account
AccountType
JournalEntry
JournalLine
Ledger
AccountingPeriod
Dimension
```

Account types:

```text
ASSET
LIABILITY
EQUITY
REVENUE
EXPENSE
```

Normal balances:

```text
DEBIT
CREDIT
```

Journal Entry statuses:

```text
DRAFT
PROPOSED
PENDING_APPROVAL
APPROVED
POSTED
REVERSED
VOIDED
```

A journal entry must contain at least two lines.

A line may contain either a debit or a credit, never both.

Debit and credit amounts must be non-negative.

---

# Immutable Ledger

The ledger represents posted accounting consequences.

Financial reports must derive from the ledger.

Do NOT calculate the trial balance, balance sheet, or income statement from arbitrary Accounting Objects.

The flow must be:

```text
Business Event
      |
      v
Accounting Object
      |
      v
Accounting Decision
      |
      v
Journal Entry
      |
      v
Validation
      |
      v
Posting
      |
      v
Immutable Ledger
      |
      +----> Trial Balance
      +----> General Ledger
      +----> Income Statement
      +----> Balance Sheet
```

---

# Accounting Objects

The Accounting Object layer is one of the project's defining features.

An Accounting Object represents a business event or accounting-relevant object.

Examples:

```text
vendor_bill
customer_invoice
customer_payment
vendor_payment
bank_transaction
expense
purchase_order
sales_order
contract
journal_entry
reconciliation
```

An Accounting Object should contain:

```text
id
object_type
status
occurred_at
source
entity_id
data
created_at
updated_at
```

Flexible business data may be stored as JSON.

However:

> Do NOT turn the entire accounting system into JSON.

The accounting ledger, accounts, journal lines, periods, and financial reporting structures remain strongly relational and validated.

Use JSON for flexible business context, not accounting truth.

---

# AI-Native Accounting Flow

The preferred conceptual flow is:

```text
Observed
   |
   v
Extracted
   |
   v
Classified
   |
   v
Proposed
   |
   v
Validated
   |
   v
Pending Approval
   |
   v
Approved
   |
   v
Posted
   |
   v
Reconciled
   |
   v
Closed
```

Not every workflow needs every state.

AI agents should generally operate in the first several states.

Human approval should be required for sensitive mutations unless the agent has explicit authorization.

---

# Agent Permissions

Implement:

```text
READ_ONLY
PROPOSER
APPROVER
POSTER
ADMIN
```

A normal AI accounting agent should initially be:

```text
READ_ONLY + PROPOSER
```

It may:

* query accounting data
* analyze data
* create proposals
* validate proposals

It should NOT automatically approve or post.

---

# Auditability

Every meaningful action must create an audit event.

Record:

```text
timestamp
actor_type
actor_id
action
object_type
object_id
input
output
result
evidence
```

Actor types:

```text
HUMAN
AGENT
SYSTEM
```

Do NOT store private model chain-of-thought.

Store concise explanations and evidence instead.

Example:

```json
{
  "action": "propose_journal_entry",
  "actor_type": "AGENT",
  "reason": "Historical AWS transactions were classified to account 6100.",
  "evidence": [
    "vendor_id=42",
    "historical_account=6100"
  ]
}
```

---

# Database

Start with SQLite.

Use SQLAlchemy so PostgreSQL can be added later.

Do not introduce database-specific SQL unless unavoidable.

Prefer SQLAlchemy expressions and portable types.

The application should not care whether it is running on SQLite or PostgreSQL.

---

# REST API

Eventually expose:

```text
GET  /accounts
GET  /accounts/{id}
GET  /accounts/{id}/balance

GET  /ledger
GET  /journal-entries/{id}

GET  /reports/trial-balance
GET  /reports/income-statement
GET  /reports/balance-sheet

POST /journal-entries
POST /journal-entries/{id}/validate
POST /journal-entries/{id}/approve
POST /journal-entries/{id}/post
POST /journal-entries/{id}/reverse

GET  /accounting-objects
POST /accounting-objects

GET  /audit-events
```

Do not build a sophisticated frontend during the initial phases.

---

# MCP

Eventually expose semantic MCP tools:

```text
get_chart_of_accounts
get_account
get_account_balance
get_trial_balance
get_general_ledger
search_transactions
get_journal_entry

propose_journal_entry
validate_journal_entry
submit_for_approval
approve_journal_entry
post_journal_entry
reverse_journal_entry

get_open_ap
get_open_ar

get_accounting_object
search_accounting_objects

get_audit_history
```

Separate read-only and mutating tools.

---

# Dataset Generator

Create deterministic accounting datasets.

Example:

```bash
erp dataset generate \
  --company acme \
  --transactions 10000 \
  --seed 42
```

The same seed must produce the same dataset.

Datasets should include:

* chart of accounts
* customers
* vendors
* departments
* locations
* classes
* journal entries
* ledger
* AP
* AR
* bank transactions

Generate realistic transactions:

* sales
* customer payments
* vendor bills
* vendor payments
* payroll
* rent
* subscriptions
* bank fees
* equipment purchases
* depreciation
* accruals
* prepayments
* refunds
* adjustments

---

# Error Injection

The benchmark environment must be able to deliberately introduce accounting problems.

Examples:

```text
duplicate invoice
wrong GL account
wrong department
wrong location
wrong accounting period
missing accrual
duplicate payment
unreconciled transaction
unusual transaction
incorrect amount
missing vendor
```

Every injected error must have known ground truth.

---

# Benchmark

The project should eventually support:

```bash
erp benchmark run
```

Tasks should be deterministic whenever possible.

Example tasks:

```text
GL-001
Get account balance.

GL-002
Calculate trial balance.

GL-003
Find unusual transactions.

GL-004
Identify incorrectly classified expenses.

AP-001
Find duplicate vendor invoices.

AP-002
Identify missing AP transactions.

AR-001
Identify outstanding customer invoices.

CLOSE-001
Identify missing accruals.

JE-001
Generate a journal entry.

JE-002
Validate a journal entry.

JE-003
Correct an invalid journal entry.
```

---

# Benchmark Metrics

Measure:

```text
accounting correctness
numerical correctness
classification accuracy
workflow accuracy
tool-use accuracy
hallucination rate
invalid-posting rate
auditability
```

Prefer deterministic evaluation.

For example:

```text
Journal Entry:
compare account IDs and debit/credit amounts

Trial Balance:
compare numerical values

Account Classification:
compare expected account ID

Workflow:
compare expected state transition
```

Do not rely on an LLM judge when exact ground truth is available.

---

# Testing Requirements

Tests are part of the implementation, not an afterthought.

At minimum test:

```text
balanced journal entry
unbalanced journal entry
single-line journal entry
debit + credit on same line
negative amount
inactive account
closed period
invalid dimension
reversal
duplicate posting
multi-line journal entry
```

Add property-based tests for accounting invariants when practical.

Critical invariant:

```text
total posted debits == total posted credits
```

Run:

```bash
pytest
ruff check .
```

before considering a phase complete.

---

# Development Process

## VERY IMPORTANT

Do not attempt to implement the entire project in one pass.

Work incrementally.

Before changing code:

1. Inspect the repository.
2. Read existing documentation.
3. Determine current implementation state.
4. Identify relevant tests.
5. Make the smallest coherent change.
6. Run tests.
7. Fix failures.
8. Update documentation.
9. Commit the completed phase.

Do not rewrite working code merely because you prefer a different architecture.

---

# Phase Management

Create:

```text
docs/
    architecture.md
    accounting-model.md
    agent-model.md
    roadmap.md

progress.md
```

`progress.md` must contain:

```text
Current Phase:
Current Status:
Completed:
In Progress:
Next:
Known Issues:
Last Verification:
```

Update it after every meaningful phase.

Create:

```text
tests/acceptance/
```

for high-level acceptance tests.

Do not delete or weaken tests merely to make the test suite pass.

---

# Git Strategy

Use small, meaningful commits.

Examples:

```text
feat: initialize accounting domain
feat: add chart of accounts
feat: add journal entry validation
feat: implement ledger posting
feat: add financial reports
feat: add accounting objects
feat: add audit events
feat: add FastAPI accounting endpoints
feat: add MCP accounting tools
feat: add deterministic dataset generator
feat: add accounting benchmark
```

Do not squash unrelated work.

Do not rewrite Git history.

Do not force-push.

Do not delete branches without explicit permission.

---

# Claude Code Behavior

## Investigate before modifying

Never speculate about the codebase.

If a file or implementation detail is relevant, read it first.

Use the repository itself as the source of truth.

---

## Default to implementation

When explicitly instructed to implement something, implement it rather than merely describing how to implement it.

Before implementation, make sure the requested behavior is understood.

---

## Avoid overengineering

Do not add:

* speculative abstractions
* unnecessary interfaces
* unnecessary dependency injection
* unnecessary configuration
* unnecessary microservices
* unnecessary async code
* unnecessary frontend infrastructure
* unnecessary generic frameworks

Build the smallest architecture that correctly supports the current phase.

---

## Accounting correctness overrides convenience

If a simple implementation would violate an accounting invariant, do not use it.

For example:

BAD:

```python
balance += amount
```

if this bypasses the ledger.

GOOD:

```text
Business Event
→ Accounting Object
→ Journal Entry
→ Validation
→ Posting
→ Ledger
```

---

# Never Hard-Code Tests

Do not implement logic specifically to satisfy a test fixture.

Tests should verify general accounting behavior.

Implement the underlying rule.

---

# Temporary Files

Temporary scripts may be created for investigation or testing.

Clean them up when no longer needed.

Do not leave experimental files throughout the repository.

---

# Phase Completion Rule

A phase is complete only when:

1. Implementation is complete.
2. Unit tests pass.
3. Integration tests pass.
4. Relevant acceptance tests pass.
5. Ruff passes.
6. Documentation is updated.
7. `progress.md` is updated.
8. Git status is understood.
9. Changes are committed.

At the end of a phase, provide a concise summary:

```text
Implemented:
Tests:
Architecture decisions:
Known issues:
Next phase:
```

---

# Phase Order

Implement in this order:

## Phase 0

Repository and development infrastructure.

## Phase 1

Chart of accounts, dimensions, periods.

## Phase 2

Journal entries and deterministic validation.

## Phase 3

Immutable ledger and financial reports.

## Phase 4

Accounting Object model and event model.

## Phase 5

Workflow/state machine.

## Phase 6

Audit/event log.

## Phase 7

FastAPI.

## Phase 8

MCP.

## Phase 9

Deterministic accounting dataset generator.

## Phase 10

Benchmark/evaluation framework.

## Phase 11

Example accounting agents.

## Phase 12

PostgreSQL compatibility.

Do not skip ahead unless explicitly instructed.

---

# Phase 0 Acceptance Criteria

Before moving to Phase 1:

```text
[ ] Python project initializes successfully
[ ] Package installs successfully
[ ] SQLite connection works
[ ] Alembic works
[ ] pytest works
[ ] Ruff works
[ ] Basic CI configuration exists
[ ] docs/architecture.md exists
[ ] docs/accounting-model.md exists
[ ] progress.md exists
```

---

# Phase 1 Acceptance Criteria

Before moving to Phase 2:

```text
[ ] Account model exists
[ ] Account hierarchy works
[ ] Account types work
[ ] Normal balances work
[ ] Accounting periods work
[ ] Dimensions work
[ ] Seed chart of accounts exists
[ ] Tests cover core invariants
```

---

# Phase 2 Acceptance Criteria

Before moving to Phase 3:

```text
[ ] Journal entries exist
[ ] Journal lines exist
[ ] Decimal arithmetic is used
[ ] Balanced entries validate
[ ] Unbalanced entries fail
[ ] Invalid accounts fail
[ ] Closed periods fail
[ ] Posted entries cannot be mutated
[ ] Reversals create new entries
```

---

# Phase 3 Acceptance Criteria

Before moving to Phase 4:

```text
[ ] Immutable ledger exists
[ ] Trial balance works
[ ] General ledger works
[ ] Income statement works
[ ] Balance sheet works
[ ] Accounting equation holds
[ ] Financial reports derive from posted ledger
```

---

# Phase 4 Acceptance Criteria

Before moving to Phase 5:

```text
[ ] AccountingObject exists
[ ] JSON business data supported
[ ] Business events supported
[ ] Object-to-accounting-impact relationship exists
[ ] Objects cannot bypass accounting validation
```

---

# Phase 5 Acceptance Criteria

Before moving to Phase 6:

```text
[ ] Workflow states exist
[ ] State transitions are validated
[ ] Agent proposals can enter workflow
[ ] Human approval can be represented
[ ] Posting requires appropriate state/permission
```

---

# Phase 6 Acceptance Criteria

Before moving to Phase 7:

```text
[ ] Audit events exist
[ ] Human/agent/system actors are distinguishable
[ ] Accounting mutations create audit events
[ ] Audit records cannot be silently modified
[ ] Evidence references can be stored
```

---

# Phase 7 Acceptance Criteria

Before moving to Phase 8:

```text
[ ] FastAPI application starts
[ ] Read endpoints work
[ ] Journal proposal endpoint works
[ ] Validation endpoint works
[ ] Approval endpoint works
[ ] Posting endpoint works
[ ] Reports are accessible
[ ] API integration tests pass
```

---

# Phase 8 Acceptance Criteria

Before moving to Phase 9:

```text
[ ] MCP server starts
[ ] Read tools work
[ ] Proposal tools work
[ ] Validation tool works
[ ] Permission checks work
[ ] MCP integration tests pass
```

---

# Phase 9 Acceptance Criteria

Before moving to Phase 10:

```text
[ ] Deterministic dataset generation works
[ ] Seed produces reproducible data
[ ] 1,000 transaction dataset works
[ ] 10,000 transaction dataset works
[ ] Generated companies balance
[ ] Error injection works
[ ] Ground truth is preserved
```

---

# Phase 10 Acceptance Criteria

Before moving to Phase 11:

```text
[ ] Benchmark task schema exists
[ ] At least 10 benchmark tasks exist
[ ] Deterministic scoring exists
[ ] Agent trajectories can be recorded
[ ] Benchmark CLI works
[ ] Results can be exported
```

---

# Phase 11 Acceptance Criteria

```text
[ ] Example investigation agent
[ ] Example JE agent
[ ] Example duplicate-invoice agent
[ ] Agents interact through tools rather than SQL
[ ] Agent actions are auditable
[ ] Benchmark can evaluate agents
```

---

# Important Product Boundary

This project is NOT:

```text
"ChatGPT for accounting."
```

It is:

```text
"An open-source accounting environment for building and evaluating AI accounting agents."
```

Optimize every architectural decision toward that objective.

---

# Future Research Direction

The architecture should eventually allow experiments comparing:

```text
frontier LLM
vs
RAG
vs
tool-augmented LLM
vs
fine-tuned model
vs
workflow-specific model
```

against identical accounting environments.

Potential research questions:

1. Do semantic Accounting Objects improve agent performance?
2. Do deterministic accounting tools reduce hallucinations?
3. Does validation substantially reduce accounting errors?
4. Can accounting workflows be learned from agent trajectories?
5. Which accounting workflows should remain deterministic?
6. Which workflows benefit from autonomous agents?
7. What level of human approval produces the best reliability?
8. Can a specialized accounting workflow model outperform a general-purpose model on accounting tasks?

Do not implement these research features prematurely.

Build the accounting environment first.

---

# Final Principle

The project should make the following possible:

```text
                 AI AGENT
                     |
                     v
             Semantic Tools
                     |
                     v
            Accounting Objects
                     |
                     v
             Workflow Engine
                     |
                     v
          Deterministic Validation
                     |
                     v
             Double-Entry GL
                     |
                     v
            Immutable Ledger
                     |
          +----------+----------+
          |          |          |
          v          v          v
       Reports    Audit Log   Benchmark
```

The accounting kernel must remain deterministic even when everything above it becomes increasingly agentic.

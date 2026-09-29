# Accounting Model

**Status:** Phases 1 to 5 are implemented; later phases are still specification.
Each section names the phase that implements it.

## Invariants

These are non-negotiable, and every phase must preserve them.

1. Every posted journal entry balances: `SUM(debits) = SUM(credits)`.
2. The accounting equation holds: `Assets = Liabilities + Equity`. Before period close,
   current-period earnings sit in revenue and expense accounts, so the working form is
   `Assets = Liabilities + Equity + (Revenue − Expenses)`.
3. A posted journal entry is never edited or deleted. It can only be reversed by another
   journal entry.
4. A closed accounting period accepts no postings.
5. All monetary amounts use `Decimal` with exactly two decimal places. Floating-point
   arithmetic is never used for amounts. See [Money](#money).
6. A journal entry has at least two lines. Each line is either a debit or a credit, never
   both, and that amount is strictly positive: there are no zero-amount lines.

System-wide consequence: total posted debits equal total posted credits at all times.

## Flow of accounting truth

```text
Business Event → Accounting Object → Accounting Decision → Journal Entry
    → Validation → Posting → Immutable Ledger → Reports
```

Financial reports (trial balance, general ledger, income statement, balance sheet) are
derived only from the posted ledger, never from Accounting Objects.

## Money

Implemented in `opensumma.money`.

- SQLite has no decimal type. A `NUMERIC` column stores `0.10` as a binary float, and
  `SUM` of 0.10 and 0.20 returns `0.30000000000000004`. Amounts are therefore stored as
  integer cents in the `Money` column type (`BIGINT`), which is exact on SQLite and
  PostgreSQL, and SQL `SUM()` stays exact too.
- `round_money()` rounds to two decimal places, with halves rounding away from zero
  (`ROUND_HALF_UP`). Amounts are rounded once, explicitly, where they enter the system,
  before validation. Floats are rejected.
- The `Money` column never rounds. It rejects any value with more than two decimal places,
  any non-`Decimal` value, and NaN or infinity. An entry validated as balanced is
  therefore exactly the entry recorded. Silent rounding on write could turn a validated,
  balanced entry into an unbalanced one.
- `ensure_money()` is the single definition of an acceptable amount, shared by the
  `Money` column and by the kernel's journal input: an exact `Decimal` in whole cents
  that fits a 64-bit column. The kernel rejects anything else instead of rounding it,
  so rounding is always an explicit decision by the caller.
- Migrations record `Money` columns as `sa.BigInteger()` and don't import application
  code.

## Currency

A single functional currency with two decimal places, and no currency column
anywhere: amounts are bare numbers in the company's own currency.

Multi-currency accounting needs a transaction amount, a functional amount, a rate, a
rate date, and revaluation gains and losses, which touches every part of the ledger.
It is out of scope, and adding it would be a deliberate redesign rather than an extra
column.

## Timestamps and dates

- Timestamps are UTC and timezone-aware in Python. `opensumma.utc.UtcDateTime` rejects
  naive values going in and re-attaches UTC coming out, because SQLite drops `tzinfo`
  silently and would otherwise return naive datetimes that no longer equal what was
  written. Ruff's `DTZ` rules keep naive `datetime` calls out of the source.
- `ensure_utc()` is the single definition of an acceptable timestamp, shared by the
  column and by services that take one, such as an Accounting Object's
  `occurred_at`: an aware `datetime`, normalised to UTC. A naive one is refused
  rather than assumed to be UTC.
- Accounting dates, such as a period's start or an entry's accounting date, are plain
  `date` values. A posting belongs to an accounting date, not to an instant.

## Accounts (Phase 1)

Implemented in `opensumma.kernel.accounts` and `opensumma.kernel.models`.

| Account type | Normal balance | Statement |
| --- | --- | --- |
| ASSET | DEBIT | Balance sheet |
| LIABILITY | CREDIT | Balance sheet |
| EQUITY | CREDIT | Balance sheet |
| REVENUE | CREDIT | Income statement |
| EXPENSE | DEBIT | Income statement |

- Accounts form a hierarchy. A child has the same account type as its parent, so a
  whole subtree reports under one type.
- **Only leaf accounts are postable; a parent exists to aggregate the accounts below
  it.** Adding a child to a postable account turns it into an aggregate, which is how a
  chart is built from the top down. An account that already has posted lines cannot
  gain children (`AccountHasPostingsError`); see
  [Accounts with postings](#accounts-with-postings).
- An account is active only if all of its ancestors are active. Deactivating an account
  deactivates everything below it. Reactivating one requires an active parent and leaves
  its descendants retired, because they may have been retired individually beforehand.
- `assert_postable()` is the single gate: it rejects aggregates and inactive accounts.
- Contra accounts, such as accumulated depreciation, carry the opposite normal balance
  to their type. The normal balance is stored per account and defaults from the type;
  `Account.is_contra` reports the difference.
- `opensumma.kernel.seed` holds a default chart of accounts, created through the same
  services as any other account so that it obeys the same rules.

## Accounting periods (Phase 1)

Implemented in `opensumma.kernel.periods`.

- Periods never overlap, so an entry's accounting date resolves to exactly one period.
  Non-overlap cannot be expressed as a portable table constraint, so `create_period()`
  enforces it and `period_for_date()` fails loudly rather than arbitrarily if two
  periods ever match.
- A period's end is not before its start, checked in Python and in the database.
- Postings are accepted only into open periods (`assert_period_open()`).
- Closing is reversible: a closed period can be reopened for corrections. The kernel's
  `close_period` and `reopen_period` do only that; the workflow engine decides who may
  close or reopen a period and in what order (see [Controls](#controls)).
- Period codes are free text, so a fiscal year need not follow the calendar.
  `create_calendar_year_periods()` covers the common case of twelve calendar months.

## Dimensions (Phase 1)

Implemented in `opensumma.kernel.dimensions`.

- A dimension is an analytical axis such as department, location, or class, with a fixed
  set of allowed values. Dimensions classify postings and never affect whether an entry
  balances.
- Value codes are unique within a dimension but not across dimensions, so DEPARTMENT and
  LOCATION may both have a value coded `HQ`.
- `resolve_dimension_value()` is the gate journal lines use from Phase 2: an unknown
  dimension, an unknown value, or a retired value is rejected rather than stored as free
  text.
- Values are deactivated rather than deleted, because postings that already reference
  them stay in the ledger forever.

## Journal entries (Phase 2)

Implemented in `opensumma.kernel.journal` and `opensumma.kernel.models`.

| Status | Meaning |
| --- | --- |
| DRAFT | Being prepared outside the workflow, such as by trusted code |
| PROPOSED | Proposed through the workflow by a preparer (human or agent), or returned to them |
| PENDING_APPROVAL | Submitted, valid, and awaiting an approver; content locked |
| APPROVED | Approved, not yet posted; content locked |
| POSTED | Recorded in the ledger; immutable from here on |
| REVERSED | Posted, then offset by a reversing entry; both stay in the ledger |
| VOIDED | Abandoned before posting; never affected the ledger |

The kernel moves entries between DRAFT, POSTED, REVERSED, and VOIDED. PROPOSED,
PENDING_APPROVAL, and APPROVED belong to the workflow engine (Phase 5), which decides
*who* may move an entry and *from which status*; see [The workflow](#the-workflow-phase-5).
The kernel posts from any status that is not yet final: the workflow wraps it rather
than changing it, and the kernel's own operations remain for trusted code. The one
thing the kernel adds for the workflow is that an entry's content is locked from
submission onward (see [Immutability](#immutability)).

An entry has an accounting date, a required description, and numbered lines. A line
names one account, carries a debit or a credit, an optional memo, and at most one value
per dimension. Its period is not stored: the accounting date determines it.

### Two tiers of rules

**Recording** (`create_journal_entry`) rejects what cannot be stored at all. The
database enforces the line shape too, so a direct write cannot store it either.

| Code | Rule |
| --- | --- |
| MISSING_DESCRIPTION | Every entry says what it records |
| TEXT_TOO_LONG | Descriptions and memos fit their columns (500 characters) |
| INVALID_AMOUNT | Amounts are `Decimal`, finite, whole cents, and fit 64 bits; never rounded |
| NEGATIVE_AMOUNT | Amounts are not negative |
| DEBIT_AND_CREDIT | A line is a debit or a credit, never both |
| ZERO_AMOUNT | A line's amount is strictly positive |
| UNKNOWN_ACCOUNT | The account exists |
| UNKNOWN_DIMENSION | The dimension exists |
| UNKNOWN_DIMENSION_VALUE | The value exists within that dimension |

**Posting** (`validate_journal_entry`, `post_journal_entry`) rejects what cannot enter
the ledger. A draft may break these rules, because it may still be incomplete and
master data may change before it is posted, so they are checked at the moment of
posting.

| Code | Rule |
| --- | --- |
| TOO_FEW_LINES | At least two lines |
| UNBALANCED | Total debits equal total credits, in exact `Decimal` arithmetic |
| NO_PERIOD | An accounting period contains the entry's date |
| PERIOD_CLOSED | That period is open |
| ACCOUNT_NOT_POSTABLE | Every account is a leaf, not an aggregate |
| ACCOUNT_INACTIVE | Every account is active |
| DIMENSION_VALUE_INACTIVE | Every dimension value is active |

Both tiers report every problem at once, as `ValidationIssue` values with a stable
code, a message, and the line number where one applies, in a deterministic order:
entry-level issues first, then by line. Callers and benchmarks compare codes rather
than parse messages, and a proposal can be corrected in a single pass.

Posting an entry that is already in the ledger raises `AlreadyPostedError`; posting a
voided one raises `EntryStatusError`. Neither is a content problem, so neither is an
issue code.

### Immutability

A POSTED, REVERSED, or VOIDED entry is final. Its only permitted change is POSTED to
REVERSED, which happens when a reversal is posted. Everything else is refused with
`ImmutableEntryError`: editing the entry, adding, removing, moving, or editing its
lines, changing their dimension values, and deleting any of them.

An entry that is PENDING_APPROVAL or APPROVED has its content locked: its date,
description, lines, and their dimension values cannot change, and it cannot be
deleted, but its status can (it is approved, rejected, posted, or voided). What an
approver approved is therefore exactly what is posted. A rejected entry returns to
PROPOSED and can be corrected again.

This is enforced below the services, on every SQLAlchemy session:

- Before each flush, a hook compares every changed journal object against the status
  its entry has *in the database*. Attribute history is not enough: once a commit has
  expired an object, SQLAlchemy no longer knows an attribute's previous value.
- The same hook refuses to let any entry enter the ledger with fewer than two lines or
  unbalanced, even one written directly through the ORM without the posting service.
  The double-entry rule therefore holds for every path into the ledger, not only the
  intended one.
- Bulk INSERT, UPDATE, and DELETE statements on journal tables are refused, because
  they would bypass the flush hook.

Raw SQL on a database connection is beyond these hooks. Agents never receive SQL
access, so that is the boundary. Database triggers were considered and rejected; see
[Settled decisions](#settled-decisions).

### Reversal

- A reversal is a new journal entry with every line's debit and credit swapped, keeping
  the accounts, memos, and dimension values, linked to the original by `reversal_of`.
  The original becomes REVERSED; both stay in the ledger and net to nothing.
- The reversal's accounting date is required, not defaulted: the original's period may
  be closed, and choosing "today" would make results depend on when code runs.
- The reversal is validated like any other entry. If it is not valid, for example
  because its date falls in a closed period, nothing changes at all.
- An entry is reversed at most once, which a unique constraint also enforces. A
  reversal is itself a posted entry and can be reversed, which reinstates the original.
- A posted entry is corrected by reversing it and posting a new entry. It is never
  voided: voiding is only for entries that never reached the ledger.

### Accounts with postings

An account that already has posted lines cannot gain child accounts, because that
would turn an account holding ledger history into an aggregate that may hold none.
Draft lines do not count; such a draft simply fails validation later.

## The ledger and financial reports (Phase 3)

Implemented in `opensumma.kernel.ledger` and `opensumma.kernel.reports`.

### The ledger

The ledger is every line of every journal entry whose status is POSTED or REVERSED. It
is not a copy of the journal: a separate table would be a second source of truth that
had to be kept in step with the first, and posted lines already have every property a
ledger needs.

- **Immutable.** A posted entry's lines never change (see [Immutability](#immutability)).
- **Append-only.** An entry never leaves the ledger once it is in: the only change a
  posted entry may undergo, POSTED to REVERSED, keeps it there. A reversal adds lines;
  it never removes any.
- **Complete.** A reversed entry stays beside its reversal, so the ledger records both
  the mistake and its correction.

Reports read the ledger only through `posted_activity`, `activity_before`,
`account_balance`, and `ledger_lines`, which all filter on those two statuses. No report
can see a draft, a proposal, a voided entry, or an Accounting Object.

### Balances and signs

- `account_balance` states a balance in the account's normal direction, so it is
  positive when the account carries its usual balance. A parent's balance is the sum of
  the accounts below it in the parent's direction, so a contra account reduces it: fixed
  assets are shown net of accumulated depreciation.
- The **trial balance** puts each account's net balance in the debit or the credit
  column, whichever it falls in, and leaves off accounts that net to nothing. Its two
  column totals are equal whenever the ledger is intact.
- The **general ledger** lists each account's lines by date, then entry, then line,
  between an opening balance brought forward and a closing balance, with a running
  balance in the account's normal direction. Lines keep their memos and dimensions.
- **Financial statements** state each amount in its section's normal direction and roll
  it up the account hierarchy, so a contra account shows as negative within its
  section: accumulated depreciation reduces assets, sales returns reduce revenue.
  Accounts whose amount is zero are left off.
- Every account the ledger touches is reported, active or not. An account retired after
  it was posted to still holds that history, and omitting it would unbalance the report.

### Dates

Reports take explicit dates and never default to "today", so a report depends only on
the ledger. The trial balance and balance sheet are *as of* a date: everything posted up
to and including it. The income statement and general ledger cover a range, inclusive
at both ends. A date range that runs backwards is an error, and so is a timestamp in
place of a date.

### Unclosed net income

Revenue and expense accounts are not yet closed into retained earnings; closing entries
are an open decision (see [Open design decisions](#open-design-decisions)). Until
then, the net income posted up to a balance sheet's
date belongs to the owners without sitting in any equity account. The balance sheet
reports it as `unclosed_net_income` and includes it in total equity:

```text
Assets = Liabilities + Equity accounts + unclosed net income
```

Double entry guarantees this equation; `BalanceSheet.is_balanced` checks it. Once
closing entries exist they move earnings into retained earnings and reduce revenue and
expense accounts to zero, so the same calculation remains correct without change.

## Accounting Objects (Phase 4)

Implemented in `opensumma.objects`.

An Accounting Object is a business document or event with accounting relevance: a
vendor bill, a customer invoice, a payment, a bank transaction. It is context for
accounting, not accounting truth. No report reads it, and it affects the ledger only
through journal entries that the kernel validates and posts.

| Field | Meaning |
| --- | --- |
| `id` | Identifier |
| `object_type` | One of the types below |
| `status` | `OBSERVED`, `EXTRACTED`, `CLASSIFIED`, or `VOIDED`; see [Status](#status) |
| `occurred_at` | When it happened in the business: a timezone-aware UTC timestamp, required and never defaulted |
| `source` | Where it was observed, such as `email`, `bank_feed`, or `vendor_portal` |
| `counterparty_id` | The vendor or customer it concerns, if known; see [Counterparties](#counterparties) |
| `data` | Business context as JSON; see [Business data](#business-data) |
| `created_at`, `updated_at` | When it was recorded and last changed here |

Object types are lowercase, as agents read and write them: `vendor_bill`,
`customer_invoice`, `customer_payment`, `vendor_payment`, `bank_transaction`,
`expense`, `purchase_order`, `sales_order`, `contract`, `journal_entry` (a request
for a manual entry, such as an accrual, as a document in its own right), and
`reconciliation`. The list is closed and constrained in the database, like the
kernel's vocabularies.

### Status

An object is `OBSERVED` from the moment it is recorded. `EXTRACTED` means its
business data has been read from its source; `CLASSIFIED` that its counterparty is
settled. It may be `VOIDED` from any of these. Which moves are allowed, and who may
make them, is the workflow engine's to decide (see [The workflow](#the-workflow-phase-5)).

Whether an object is recorded in the ledger is never a stored status. It is derived
from the journal entries that record it (see [Accounting impact](#accounting-impact)),
so an object cannot claim to be posted when the ledger says otherwise.

### Counterparties

A counterparty is a vendor or a customer: someone the company does business with.
There is one company per database, so the company itself is never a counterparty,
and nothing needs a company id. Counterparties are master data, like accounts: coded,
named, deactivated rather than deleted, and seeded with a fixed cast of six vendors
and five customers (`seed_counterparties`), fixed rather than random so that anything
built on it is reproducible.

An object names its counterparty by code, through `resolve_counterparty`, which
requires the counterparty to exist, to be active, and to be the kind the object's type
calls for:

| Object types | Counterparty |
| --- | --- |
| `vendor_bill`, `vendor_payment`, `purchase_order` | A vendor |
| `customer_invoice`, `customer_payment`, `sales_order` | A customer |
| All others | Either, or none |

An object need not name a counterparty when it is recorded, because the source may
not say (a bank line reading "AMZN MKTP"); settling it is what classifying an object
means. The workflow then refuses to classify a vendor bill without a vendor, or to
propose an entry for one.

### Business data

- `data` is a JSON object. It may hold strings, integers that fit 64 bits, booleans,
  null, arrays, and nested objects.
- **Floats are refused**, and so is `Decimal`. A JSON number with a fraction comes
  back as a binary float, which must never stand in for an amount. Amounts are
  written as strings, such as `"1200.00"`, and read with `Decimal` where they are
  used. The `BusinessData` column type enforces this on every write through
  SQLAlchemy, bulk statements included; `ensure_business_data()` is the single
  definition of what is acceptable, with the path to any bad value in its message.
- Values JSON would not return exactly, such as tuples, sets, dates, and non-string
  keys, are refused rather than converted.
- Data is copied on the way in, so later changes to the caller's dictionary do not
  alter what was validated. It is replaced rather than edited in place: SQLAlchemy
  does not notice a change made inside a JSON value, so assign a new value.
- Top-level string fields are queryable, portably on SQLite and PostgreSQL:
  `search_accounting_objects(data={"invoice_number": "INV-1001"})`.
- Amounts in business data never reach a report. If a bill arrives twice, the
  objects' data claims the amount twice; the ledger records what was posted.

### Business events

An `AccountingEvent` is something that happened to an object in the business: a
bill `received` or `disputed`, goods on a purchase order `goods_received`, a contract
`signed`. It has an `event_type` (lowercase snake_case, from an open vocabulary), an
`occurred_at` in business time, a `source`, JSON `data`, and a `created_at` recording
when it was entered here. An object's own `occurred_at` and `source` describe how it
came into being; its events are what happened to it afterwards, in business-time
order.

Three kinds of event are kept apart:

| Kind | Records | Phase |
| --- | --- | --- |
| Business event | Something that happened in the world | 4 |
| Workflow state | Where the system is in processing an object | 5 |
| Audit event | Who did what in the system, and why | 6 |

Business events are facts, so they are never changed or deleted; a mistaken one is
superseded by a later event. A voided object can still receive events, because the
world does not stop when an object is withdrawn.

### Accounting impact

An object is linked to the journal entries that record its accounting impact. The
link is many-to-many: a bill may be recorded by an entry and later by its
correction, and one payment entry may settle two bills.

- **The link points from objects to entries.** The kernel's tables hold no reference
  to objects, so linking changes nothing in the ledger, a posted entry can be linked
  after the fact, and the kernel remains usable without this layer.
- **Entries are made by the kernel.** `create_journal_entry_for_object` records the
  accounting decision as a draft through `create_journal_entry`, so the recording
  rules apply, and links it. Posting is the kernel's `post_journal_entry`, with its
  posting rules. There is no other way from an object to the ledger.
- **Impact is derived.** `accounting_impact` collects the linked entries and every
  reversal of them, linked or not, and reads what they posted through the ledger
  (`posted_activity(entry_ids=...)`). An entry and its reversal therefore net to
  nothing for the object as they do in the ledger. Drafts and voided entries
  contribute nothing, and `has_net_impact` says whether the ledger still carries a
  balance for the object.
- **Impact is per entry, not apportioned.** A payment entry that settles two bills
  appears in full in each bill's impact.
- **Links are permanent history.** A wrong entry is reversed or voided, never
  unlinked, and the database refuses to delete a linked draft.

### Voiding

An object is voided, never deleted: a duplicate bill stays on record as a withdrawn
duplicate.

- An object can be voided only when the ledger no longer carries it: no linked entry
  that could still be posted, and a net impact of zero on every account. Drafts are
  voided and posted entries reversed through the kernel first. Voiding an object
  therefore never removes anything from the ledger.
- A voided object is final. It cannot change and no entry can be linked to it, which
  the session hooks enforce as well as the services.
- The kernel does not know about objects, so it does not stop a reversal of a
  reversal from reinstating an entry that records a voided object. The object's
  `accounting_impact` would then show `has_net_impact`, which is how such a
  contradiction is detected.

### Why objects cannot bypass validation

1. The kernel never imports this layer, which a test checks, so no report can read an
   object.
2. The only way from an object to the ledger is a journal entry that the kernel
   records, validates, and posts.
3. No ledger state is stored on an object; its impact is derived from the ledger.
4. Voiding an object cannot remove its impact; only kernel reversals can.
5. Object history is guarded below the services, on every session: objects are never
   deleted, events and links are never changed or deleted, voided objects are final,
   and bulk writes to these tables are refused because they would bypass the flush
   hook.

## The workflow (Phase 5)

Implemented in `opensumma.workflow`.

The workflow decides *who* may move an accounting subject from one state to another,
and *when*. The kernel and the object layer still decide whether the content is
acceptable; the workflow wraps them rather than changing them.

### The accounting flow

The AI-native flow from the project's specification maps onto three subjects:

| Stage | Where it lives |
| --- | --- |
| Observed | Object `OBSERVED` |
| Extracted | Object `EXTRACTED`: business data read from the source |
| Classified | Object `CLASSIFIED`: counterparty settled |
| Proposed | Journal entry `PROPOSED`, linked to the object |
| Validated | The check `submit_for_approval` requires; no entry is submitted with issues |
| Pending Approval | Journal entry `PENDING_APPROVAL` |
| Approved | Journal entry `APPROVED` |
| Posted | Journal entry `POSTED`; the object's impact is derived from it |
| Reconciled | Not yet: bank reconciliation is a later feature |
| Closed | Accounting period `CLOSED` |

Not every subject needs every stage: a bank transaction from a structured feed can be
classified without being extracted, and a journal entry need not concern an object.

### Transitions

Every move is a row in a transition table (`JOURNAL_ENTRY_WORKFLOW`,
`ACCOUNTING_OBJECT_WORKFLOW`, `ACCOUNTING_PERIOD_WORKFLOW`): an action, the states it
may start from, the state it leads to, and the permission it needs.

| Subject | Action | From | To | Permission |
| --- | --- | --- | --- | --- |
| Entry | propose | (new) | PROPOSED | PROPOSER |
| Entry | submit | DRAFT, PROPOSED | PENDING_APPROVAL | PROPOSER |
| Entry | approve | PENDING_APPROVAL | APPROVED | APPROVER |
| Entry | reject | PENDING_APPROVAL | PROPOSED | APPROVER |
| Entry | post | APPROVED | POSTED | POSTER |
| Entry | reverse | POSTED | REVERSED (and a new POSTED reversal) | POSTER |
| Entry | void | DRAFT, PROPOSED | VOIDED | PROPOSER |
| Entry | void | PENDING_APPROVAL, APPROVED | VOIDED | APPROVER |
| Object | observe | (new) | OBSERVED | PROPOSER |
| Object | extract | OBSERVED, EXTRACTED, CLASSIFIED | EXTRACTED | PROPOSER |
| Object | classify | OBSERVED, EXTRACTED, CLASSIFIED | CLASSIFIED | PROPOSER |
| Object | void | OBSERVED, EXTRACTED, CLASSIFIED | VOIDED | APPROVER |
| Period | close | OPEN | CLOSED | ADMIN |
| Period | reopen | CLOSED | OPEN | ADMIN |

Each operation checks, in this order, and changes nothing if any check fails:

1. the action is allowed from the current state (`InvalidTransitionError`);
2. the actor is active and holds the permission (`PermissionDeniedError`);
3. a reason is given where one is required: to reject, void, or reopen;
4. the workflow's controls, below;
5. the content, through the kernel's or the object layer's own rules.

### Controls

- **Posting needs approval.** An entry is posted only from APPROVED, only by a POSTER,
  and still through the kernel's posting rules: a period closed after approval stops
  it.
- **Segregation of duties.** An entry is never approved by an actor who proposed or
  submitted it, even one who also holds APPROVER.
- **Validation gates submission and approval.** An entry is submitted only if it
  passes validation, and validated again when approved, because master data may have
  changed in between.
- **Counterparties before accounting.** A vendor bill is classified only with a
  vendor, and an entry is proposed for it only once its vendor is settled; likewise
  for customer documents.
- **Periods close in order.** A period closes only when every earlier period is
  closed, and reopens only when every later one is open. The closed periods are
  therefore always the earliest, every posting falls after them, and a closed
  period's reports never change.
- **No stranded entries.** A period closes only when no entry dated in it could still
  be posted: drafts, proposals, and pending or approved entries are posted or voided
  first.

### Trusted layers and actors

The kernel's and object layer's operations remain available, unguarded, to trusted
code such as the dataset generator (Phase 9), which must be able to create exactly
the mistakes the controls exist to catch, like a bill booked without a vendor. Every
interface an actor can reach, REST (Phase 7) and MCP (Phase 8), goes through the
workflow. The ledger's own invariants hold on every path either way.

### History

Every transition is recorded: the subject, the action, the states before and after,
the actor, an optional reason, and when. The history is append-only, guarded like
posted entries. It records state changes only; the audit log (Phase 6) will record
every meaningful action, including refused attempts, with inputs, outputs, and
evidence.

## Settled decisions

| Decision | Resolution |
| --- | --- |
| Currencies | One functional currency, two decimals, no currency column |
| Zero-amount lines | Not allowed; each line has exactly one strictly positive side |
| Posting to parent accounts | Only leaf accounts are postable; parents aggregate |
| Timestamps | UTC only, timezone-aware; accounting dates are plain dates |
| Rounding at the kernel | Never; sub-cent amounts are rejected, callers round explicitly |
| Correcting a posted entry | Reverse it and post a new one; never edit or void it |
| Reversal date | Required from the caller; never defaulted |
| The ledger | Posted journal lines, not a separate copied table |
| Database triggers for immutability | Not used; session hooks enforce it (below) |
| Report dates | Always explicit; a report never depends on today's date |
| Companies | One company per database; no table carries a company id |
| Counterparties | Vendors and customers are master data; objects reference them by `counterparty_id` (Phase 5 replaced the free-text `entity_id`) |
| Counterparty kind | Set by the object type where it implies one; a vendor bill names a vendor |
| Object status and the ledger | Status says whether an object stands; whether it is recorded is derived from its entries |
| Floats in business data | Refused; amounts are strings such as `"1200.00"` |
| Object to journal entry | A many-to-many link owned by the object layer; kernel tables unchanged |
| Deleting objects | Never; objects are voided, events and links are permanent |
| Who moves entries through the workflow | Actors holding explicit permissions; none implies another |
| Approving one's own work | Never; the approver must not have proposed or submitted the entry |
| Content under review | Locked from submission; a rejection unlocks it |
| Period close order | Periods close in order and reopen in reverse, so closed periods' reports never change |
| Closing over unposted entries | Refused; they are posted or voided first |

## Open design decisions

These should be settled before or during the phase named.

1. **Required dimensions (Phase 9 or later).** Whether a dimension may be mandatory,
   globally or per account, is still open. The general ledger shows each line's
   dimensions, but no report is broken down by them yet. The dataset generator and the
   "wrong department" benchmark tasks will show which rule is useful.
2. **Closing entries (a later phase).** Year-end close moves net income into retained
   earnings. It needs closing entries marked as such, so that the income statement
   for the closed year still shows its income, which touches the reports as well as
   the workflow. Until then the balance sheet carries net income as
   `unclosed_net_income`, which stays correct.
3. **Typed business data (Phase 9).** Whether each object type requires fields, such
   as a vendor bill's invoice number and amount, is open. The dataset generator will
   show what each type needs.
4. **Settlement (Phase 8).** Which payment settles which bill, and for how much, is
   what `get_open_ap` and `get_open_ar` need. Links to journal entries do not express
   it; that needs a relationship between objects, with amounts.
5. **Employees as counterparties (Phase 9).** Expense reports and payroll concern
   employees, who are neither vendors nor customers. Whether they become a third
   counterparty kind is for the dataset generator to show.

Database triggers for immutability were decided against in Phase 3. CLAUDE.md allows
database-specific SQL only where it is unavoidable, and here it is avoidable: the only
writers are the kernel's own sessions, agents never get SQL access, and raw-SQL
tampering by someone with direct database access is outside the system's boundary. The
trial balance and balance sheet totals would expose most such tampering in any case.

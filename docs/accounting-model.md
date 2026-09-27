# Accounting Model

**Status:** Phase 1 is implemented; later phases are still specification. Each
section names the phase that implements it.

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
  chart is built from the top down. From Phase 2 on, an account that already carries
  postings must not be allowed to gain children.
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
- Closing is reversible: a closed period can be reopened for corrections. Who may do so
  is a Phase 5 question.
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

| Status | Meaning |
| --- | --- |
| DRAFT | Being prepared; not yet submitted |
| PROPOSED | Submitted by a preparer (human or agent) |
| PENDING_APPROVAL | Awaiting an approver |
| APPROVED | Approved, not yet posted |
| POSTED | Recorded in the ledger; immutable from here on |
| REVERSED | Posted, then offset by a reversing entry; both stay in the ledger |
| VOIDED | Abandoned before posting; never affected the ledger |

- A reversal is a new journal entry with debits and credits swapped, linked to the
  original. The original's lines remain in the ledger.
- Posting an entry that is already posted is rejected.

## Accounting Objects (Phase 4)

An Accounting Object represents a business event or accounting-relevant document, such
as a vendor bill, customer invoice, payment, or bank transaction. Its fields are `id`,
`object_type`, `status`, `occurred_at`, `source`, `entity_id`, `data`, `created_at`, and
`updated_at`.

`data` holds flexible business context as JSON. Accounting truth (accounts, journal
lines, periods, the ledger) stays strictly relational and validated. Objects affect the
ledger only through journal entries, which go through normal validation.

## Settled decisions

| Decision | Resolution |
| --- | --- |
| Currencies | One functional currency, two decimals, no currency column |
| Zero-amount lines | Not allowed; each line has exactly one strictly positive side |
| Posting to parent accounts | Only leaf accounts are postable; parents aggregate |
| Timestamps | UTC only, timezone-aware; accounting dates are plain dates |

## Open design decisions

These should be settled before or during the phase named.

1. **Multiple entities (Phase 4 or later).** There is one implicit company. Accounting
   Objects carry an `entity_id`, and the dataset generator produces one company at a
   time. Whether companies share a database or each gets its own is undecided.
2. **Sequential period close (Phase 5).** Closing a period does not require earlier
   periods to be closed, and reopening is unrestricted. Both belong with the workflow
   engine and its permissions rather than with the kernel.
3. **Required dimensions (Phase 2).** Whether a dimension may be mandatory, globally or
   per account, is deferred until journal lines exist to carry them.

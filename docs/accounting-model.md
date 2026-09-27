# Accounting Model

**Status:** specification. None of this is implemented yet. It is the contract that
Phases 1–4 implement; each section names its phase.

## Invariants

These are non-negotiable, and every phase must preserve them.

1. Every posted journal entry balances: `SUM(debits) = SUM(credits)`.
2. The accounting equation holds: `Assets = Liabilities + Equity`. Before period close,
   current-period earnings sit in revenue and expense accounts, so the working form is
   `Assets = Liabilities + Equity + (Revenue − Expenses)`.
3. A posted journal entry is never edited or deleted. It can only be reversed by another
   journal entry.
4. A closed accounting period accepts no postings.
5. All monetary amounts use `Decimal`. Floating-point arithmetic is never used for
   amounts.
6. A journal entry has at least two lines. Each line is either a debit or a credit, never
   both, and amounts are non-negative.

System-wide consequence: total posted debits equal total posted credits at all times.

## Flow of accounting truth

```text
Business Event → Accounting Object → Accounting Decision → Journal Entry
    → Validation → Posting → Immutable Ledger → Reports
```

Financial reports (trial balance, general ledger, income statement, balance sheet) are
derived only from the posted ledger, never from Accounting Objects.

## Accounts (Phase 1)

| Account type | Normal balance | Statement |
| --- | --- | --- |
| ASSET | DEBIT | Balance sheet |
| LIABILITY | CREDIT | Balance sheet |
| EQUITY | CREDIT | Balance sheet |
| REVENUE | CREDIT | Income statement |
| EXPENSE | DEBIT | Income statement |

- Accounts form a hierarchy (parent/child).
- Inactive accounts cannot receive postings.
- Contra accounts, such as accumulated depreciation, carry the opposite normal balance
  to their type. The planned design stores the normal balance per account, defaulting
  from the type.

## Accounting periods (Phase 1)

- Periods do not overlap. An entry's accounting date determines its period.
- Postings are accepted only into open periods.

## Dimensions (Phase 1)

- Journal lines may carry analytical dimensions such as department, location, and class.
- A line referencing an unknown or invalid dimension value is rejected.

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

## Open design decisions

These should be settled before or during the phase named.

1. **How money is stored (Phase 2).** SQLite has no decimal type, and SQLAlchemy's
   `Numeric` on SQLite converts through float. The test configuration turns
   SQLAlchemy's warning about this into an error. The options:
   - *Integer minor units* (`BigInteger`, converted to and from `Decimal` at the
     persistence boundary). This is exact everywhere, and SQL `SUM()` stays exact on both
     SQLite and PostgreSQL. It needs a defined scale per currency.
   - *String-backed `Decimal` type on SQLite*, with `Numeric` on PostgreSQL. Storage is
     exact, but SQL aggregation doesn't work on SQLite.

   Recommendation: integer minor units.
2. **Currencies (Phase 1/2).** The recommendation is a single functional currency per
   company at first, with multi-currency deferred.
3. **Zero-amount lines (Phase 2).** The recommendation is that each line has exactly one
   strictly positive side.
4. **Posting to parent accounts (Phase 1).** The recommendation is that only leaf
   accounts are postable, and parents aggregate.
5. **Timestamps.** Store them in UTC and keep them timezone-aware in Python (Ruff's `DTZ`
   rules enforce this). Accounting dates are plain `date` values, not timestamps.

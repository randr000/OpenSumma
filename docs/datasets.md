# Datasets

A dataset is a company's year of books with known errors in them, generated
deterministically from a seed. It is the environment an accounting agent works in,
and its ground truth is what the agent is scored against (Phase 10).

```bash
erp dataset generate --company acme --transactions 10000 --seed 42
```

The same command always writes the same dataset. The generator lives in
`opensumma.datasets`; `write_dataset`, `generate_dataset`, and `plan_dataset` are its
Python API.

## What a dataset contains

Three files, by default in `datasets/<company>/`:

| File | What it is | Who reads it |
| --- | --- | --- |
| `books.db` | The books: a SQLite database at the current schema, ready for the REST and MCP interfaces | The agent, through the interfaces |
| `manifest.json` | How it was generated, its fingerprint, its record counts, and its year-end position | People and the benchmark |
| `ground_truth.json` | Every injected error: the entries as recorded and as they should be, and how far each account is off | The benchmark only |

Give an agent a copy of `books.db` and nothing else. The books do not reveal which
records are wrong; the audit log holds a single `generate_dataset` event by the
system, which records the company, year, and transaction count, but not the seed or
the number of errors.

The books hold:

- the default chart of accounts (41 accounts) and dimensions (department, location,
  class), and the twelve monthly periods of the year, all open;
- 11 vendors and between 5 and 65 customers (about one per 150 transactions);
- one journal entry per transaction, posted, with the accrual entries reversed;
- the business documents those entries record, as accounting objects: customer
  invoices and payments, vendor bills and payments, card expense receipts, payroll
  registers, and accrual and adjustment requests;
- the operating account's bank statement, one `bank_transaction` object per line.

## The business

The company sells fleet-monitoring hardware and the services around it. Its year has
two parts.

**A fixed schedule** of 237 transactions, the same for every company: opening
balances on 1 January; monthly rent, cloud hosting, and software bills, each paid the
same month; an annual insurance premium, prepaid and amortized monthly; monthly
inventory replenishment for the previous month's usage; legal fees accrued at each
month end, the accrual reversed on the first of the next month, when the bill
arrives; payroll on the 15th and the last business day, each funded by a transfer to
the payroll account, with payroll taxes remitted on the 10th of the next month;
monthly depreciation; and monthly bank charges.

**A variable business** that fills the rest of the count exactly: sales invoices for
hardware (with their cost of goods sold) or services, collected 20 to 70 days later
and occasionally partly refunded; one-off vendor bills, paid about 30 days later;
equipment purchases, depreciated over three years; card expenses; and inventory and
reclassification adjustments. Invoices and bills that fall due after year end stay
open, so the books carry receivables and payables at 31 December.

Sales are planned first, and the fixed costs and opening balances are sized as shares
of the gross profit they earn, so every company, whatever its size and customer mix,
has a plausible margin and never runs out of cash or inventory.

Every movement of the operating bank account (1111) appears on the bank statement,
posted on the same day or a few business days later. Bank charges, payroll transfers,
and payroll tax payments are recorded from their statement lines, which the entries
record. Every other statement line is left `EXTRACTED` and unmatched, for an agent to
reconcile.

`--transactions` counts the journal entries of the **clean** books. Some errors add
entries (a duplicate) and some leave entries out (a missing accrual), so the books
may hold slightly more or fewer; the manifest gives the actual count.

## Errors

By default one error is injected per 100 transactions, and at least one of each type,
taken in turn; `--errors N` asks for another number, and `--errors 0` gives clean
books. No type takes more than a quarter of the transactions it could apply to, and
each transaction takes part in at most one error, together with the documents that
settle it, so every error can be read and corrected on its own.

| Type | What is wrong | Evidence in the books |
| --- | --- | --- |
| `duplicate_invoice` | A vendor bill received twice, through another channel days later, and recorded both times | Two `vendor_bill` objects with the same vendor, invoice number, and amount |
| `wrong_gl_account` | A bill or card expense posted to an account easily confused with the right one: 5200 and 6100, or 6500 and 6700 | The document's category, and the vendor's other bills |
| `wrong_department` | An expense line tagged with a department the document does not name | The document's `department` |
| `wrong_location` | An expense line tagged with a location the document does not name | The document's `location` |
| `wrong_accounting_period` | A bill recorded in the month before or after its invoice date | The document's `invoice_date` |
| `missing_accrual` | A month's legal fees neither accrued nor reversed | The next month's bill for that `service_period`, and the other months' accruals |
| `duplicate_payment` | A bill paid twice | Two `vendor_payment` objects for one invoice, and two bank debits |
| `unreconciled_transaction` | A customer payment the bank received that the books never recorded | A statement line no entry matches, and an invoice still open |
| `unusual_transaction` | A card charge 25 to 60 times the most the company usually spends at that merchant, on a weekend | The amount, the day, and the merchant's other charges |
| `incorrect_amount` | A bill recorded with two neighbouring digits of its amount swapped | The document's `amount` |
| `missing_vendor` | A bill recorded without naming its vendor | An `EXTRACTED` bill with no counterparty, whose data names the vendor |

## Ground truth

`ground_truth.json` names the dataset it belongs to (company, year, seed, transaction
count, and the books' fingerprint) and lists the errors in the order of the period
they misstate:

```json
{
  "id": "ERR-004",
  "type": "wrong_gl_account",
  "summary": "Card purchase - Copperleaf Bistro was posted to account 6700 instead of 6500.",
  "period": "2026-10",
  "journal_entry_ids": [812],
  "accounting_object_ids": [1187, 1188],
  "actual": [{"id": 812, "entry_date": "2026-10-14", "description": "...", "lines": [...]}],
  "expected": [{"id": 812, "entry_date": "2026-10-14", "description": "...", "lines": [...]}],
  "misstatement": {"as_of": "2026-10-31", "accounts": {"6500": "-24.58", "6700": "24.58"}},
  "details": {"journal_entry_id": 812, "line_number": 1, "posted_account": "6700", "correct_account": "6500"}
}
```

- `journal_entry_ids` and `accounting_object_ids` are the records in the books the
  error concerns: its entries as recorded, their documents and statement lines, and
  any statement line it names.
- `actual` are the entries as the books hold them and `expected` as they should. An
  entry the books should not hold (a duplicate) appears only in `actual`; one they
  are missing (an accrual) only in `expected`, with `id` null; one that is wrong
  appears in both, with the same `id`.
- `misstatement` is how far each account is off, debits positive, at the end of the
  first period the error affects. A misdated entry misstates that period and nothing
  after it; a dimension or vendor error misstates no account.
- `details` names what a scorer compares exactly: the account posted and the correct
  one, the original and the duplicate, the posted and correct amounts or dates.

The clean books of the same seed differ from the errored ones by exactly the ground
truth: at every month end, each account's balance in the errored books, less its
balance in the clean books, is the sum of `actual` less `expected` over every error.
The tests check this for every dataset they generate.

## Determinism

- Every random draw comes from `random.Random.random()`, whose sequence Python
  guarantees for a seed, never from `randint`, `choice`, or `shuffle`, whose
  algorithms may change between versions, and never from a transcendental function.
  Each concern (customers, sales, bills, card expenses, errors, ...) has its own
  stream, seeded by the seed and its name, so the business beneath the errors is the
  same with or without them.
- Records are written in date order into fresh books, so ids are the same every time.
- The fingerprint in the manifest is a SHA-256 hash of the books' content in a
  canonical order: accounts, dimensions, counterparties, periods, journal entries,
  and accounting objects. Timestamps of when rows were written or posted, and the
  audit log, are left out, since they record when the generator ran.
- A test pins the fingerprint and ground truth of one small dataset, and CI runs it
  on Python 3.12 and 3.13. A change to the generator that changes its output must
  bump `GENERATOR_VERSION`, which both files record.

## How it is recorded

The generator is trusted code. It records through the kernel and the object layer,
beneath the workflow, because the workflow's controls would refuse the very errors
it must create: a bill without a vendor, a duplicate. Every entry is still created,
validated, and posted by the kernel, and every flush passes the hooks that guard the
ledger and object history. Documents are stored as already processed (`CLASSIFIED`),
except bank statement lines awaiting a match and the bills missing a vendor
(`EXTRACTED`).

The whole generation is one audited action, `generate_dataset`, rather than an audit
event per row. Autoflush is suspended while recording and the session is flushed
every 500 records instead, so the ledger's guards run once per batch. On the
development machine a 1,000-transaction dataset takes about 5 seconds and a
10,000-transaction one about 35.

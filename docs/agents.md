# Example agents

Three example agents show how an accounting agent is built on OpenSumma, and give
the benchmark its first agents to measure. They live in `opensumma.agents`:

| Agent | Name | Tasks | Changes the books? |
| --- | --- | --- | --- |
| `InvestigationAgent` | `investigator` | GL-001 to GL-006, AP-002, AP-004, AR-001, AR-002, CLOSE-001 | No |
| `JournalEntryAgent` | `journal-entry` | JE-001, JE-002, JE-003 | Proposes, validates, submits, voids |
| `DuplicateInvoiceAgent` | `duplicate-invoice` | AP-001, AP-003 | No |

`ExampleAgents` (`examples`) is the three as one agent, each task going to the
agent that handles it. Every task has exactly one.

```bash
erp benchmark run --agent examples                   # all sixteen tasks
erp benchmark run --agent investigator               # one agent; it declines the rest
erp benchmark run --agent journal-entry --tasks JE-001,JE-002,JE-003
```

None of them uses a language model. Each applies explicit accounting rules to what
the books hold, so they are deterministic, and whatever they miss or mistake is the
rule's doing. That makes them baselines for the research questions the project is
built for: a model-driven agent can be measured against them on the same datasets,
and the tasks they solve with a few rules say which workflows need no model at all.

## What they see and do

An example agent sees exactly what any agent connected to the MCP server sees: the
books through the semantic tools, as the actor `agent` with READ_ONLY and PROPOSER.
It never touches the database, the dataset's files, or its ground truth. A test
holds them to this: the `opensumma.agents` modules may import only what the
benchmark hands an agent (`TaskPrompt`, `Tools`, `ToolCall`), each other, and the
parts of the standard library that compute (`collections`, `dataclasses`,
`datetime`, `decimal`, `re`, `typing`), so nothing in them can reach a database,
a file, a process, or the network.

- **Reads** go through the read-only tools. `Books` reads each thing once per task,
  such as the ledger (`get_ledger`) or the vendor bills
  (`search_accounting_objects`), and indexes it: an object's posted entry, the
  period a date falls in.
- **Changes** go through the workflow's tools, and each carries a concise reason and
  evidence references, which the audit log keeps. Only the journal entry agent
  changes anything, and it cannot approve or post: its entries wait for a person.
- **Findings** are reported beside the answer: each item the answer lists comes
  with a reason and the evidence for it, in a `findings` field the benchmark does
  not score but records in `results.json`:

  ```json
  {
    "journal_entry_ids": [594],
    "findings": [{
      "item": 594,
      "reason": "A charge of 10000.00 at Metro Rideshare, far above the rest of the 24 charges at Metro Rideshare, whose typical charge is 34.05.",
      "evidence": ["journal_entry_id=594", "accounting_object_id=842", "merchant=Metro Rideshare", "amount=10000.00", "typical_amount=34.05"]
    }]
  }
  ```

- **Refusals** stop the agent: a tool that refuses a call it needs raises
  `ToolRefusedError`, naming the tool and the refusal, which the benchmark records
  as the agent's error.

Each agent picks its workflow by the task's id, and reads what the task is about
(an account, a date, an entry, a bill) from the instructions with regular
expressions, so it depends on their wording, as the benchmark's notes warn any
scripted agent does.

## The investigation agent

It reads balances (GL-001) and the trial balance (GL-002) from the reports, and
lists the invoices that the customer payments recorded by year end leave unpaid,
with what is left to pay on them (AR-001). To find errors it applies one rule per
kind, each to the business documents and the posted ledger together:

| Task | Rule |
| --- | --- |
| GL-003 | A company card charge is unusual when it lies above a gap of an order of magnitude in the charges at its merchant, among no more charges than lie below the gap, and is ten times the merchant's typical charge (the median; of an even number, the geometric mean of the middle two). A merchant with fewer than three charges is too little to judge by, so its charges are compared with all the company's card charges |
| GL-004 | Documents of one category, such as meals or hosting, are posted to one account. Where more than half of a category's documents, and at least three, agree on the account, one posted elsewhere is misclassified, and the account they agree on is the right one |
| GL-005 | An expense line tagged with a department or location other than the one its document names |
| GL-006 | A bill recorded in a period other than the one its invoice date falls in |
| AP-002 | A bill recorded without a vendor is matched to the vendor whose name it gives |
| AP-004 | A bill whose entry records another amount than the bill |
| AR-002 | The bank statement is reconciled to the account it is for, found as the one account every recorded statement line's entry posts to. In date order, each deposit is matched to the latest unmatched receipt in that account of the same amount, recorded within the week before the bank received it; a deposit nothing matches was never recorded |
| CLOSE-001 | A month of the year that no accrual document covers |

Vendor bills are not judged by the unusual-charge rule. A vendor bills for different
things, a subscription beside one-off work, or stock ordered to demand, so bills
from one vendor differ by twenty times or more in ordinary books; card charges at
one merchant do not.

## The journal entry agent

For a bill (JE-001) it learns how the company records that vendor's bills from the
books, not from a table of its own: it reads the vendor's latest posted bills, up
to ten, and takes the way most of them were recorded, the account debited, the
account credited, and the tags each line carries. The bill gives the amount, the
date, and each tag's value, from its field of the same name. It proposes the entry
linked to the bill, with the precedent entries as evidence; validates it; and
submits it for approval only if it is valid:

```text
propose_journal_entry   reason: Recorded as 10 of V-PAPER's latest 10 posted bills were:
                                debit 6700, credit 2110, tagged as the bill names
                        evidence: accounting_object_id=1436, invoice=PTO-396563, vendor=V-PAPER,
                                  historical_debit_account=6700, historical_credit_account=2110,
                                  precedent_journal_entry_id=993, ...
validate_journal_entry  -> valid
submit_for_approval     -> PENDING_APPROVAL
```

A vendor with no posted bill gives it nothing to learn from, and it does not guess:
it stops, saying so.

To check a colleague's entry (JE-002) it validates it through the workflow and
reports the issue codes. To correct one (JE-003) it validates it, voids it with the
issue codes as its reason and evidence, and records the bill the entry was for
afresh, as above. An entry that turns out valid is left alone.

## The duplicate-invoice agent

A vendor numbers its invoices uniquely, so two bills from one vendor with the same
invoice number are one bill recorded twice, whatever their amounts, dates, or the
channel each came through; every copy received after the first is a duplicate
(AP-001). Invoice numbers are compared as their letters and digits, ignoring case,
spaces, and punctuation, and the vendor is the one the bill names, which a bill
recorded without its vendor still gives. A payment is a duplicate when it is made
for a bill already paid in full (AP-003).

## How they score

On the standard benchmark dataset (1,000 transactions, seed 42), the three agents
together score 1.0 on all sixteen tasks, with every metric at its best: no call
rejected, no record named that the books do not hold, no change refused, and every
change documented. On their own, each scores 1.0 on its tasks and 0 on those it
declines, so `investigator` scores 0.6875 overall, `journal-entry` 0.1875, and
`duplicate-invoice` 0.125.

The rules were written from the evidence [datasets.md](datasets.md) says each error
leaves in the books, never from a ground truth, and then checked against the ground
truth of fifteen other datasets, of 300 to 2,000 transactions, seeds from 1 to
2026, and up to one error per fifteen transactions, and against the benchmark's
books generated without errors. They raise no false alarm on clean books. They fall
short in two places:

- **Twin deposits.** Two deposits of the same amount on the same day, neither
  naming its payer, one recorded and one not, cannot be told apart from the
  statement, so the agent may name the wrong one. It happened in one of the
  datasets (AR-002 scored 0.8).
- **No precedent.** In a company of 300 transactions a vendor may have no posted
  bill yet, and JE-001 and JE-003 ask for its bill to be recorded as its other
  bills are. The journal entry agent declines to guess, and scores 0. This
  happened in two of the nine 300-transaction datasets.

That rules this simple find every injected error is itself a measurement: it says
the errors are easy to find once one knows what evidence to read, as the benchmark's
notes suspected. The difficulty for an agent lies in knowing what to read, and in
reading it through the tools, which is what a model-driven agent will be measured on.

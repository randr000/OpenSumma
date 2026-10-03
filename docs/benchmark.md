# Benchmark

The benchmark measures how well an AI agent does accounting work when it can act only
through the semantic tools. Every task has an exact expected answer, taken from a
generated dataset's books or its ground truth, and every answer is scored
deterministically: no model judges another.

```bash
erp benchmark run                                   # the oracle, on the standard dataset
erp benchmark run --agent mypackage.agents:MyAgent --dataset datasets/acme
erp benchmark tasks                                 # the task list; --json for schemas
```

Without `--dataset`, the run uses `datasets/benchmark`, generating the standard
dataset there first if it is missing: 1,000 transactions, seed 42, with the default
errors. Without `--agent`, it runs the oracle. Without `--output`, it writes to a new
directory in `results/`. `--tasks GL-001,JE-002` runs some tasks only.

## How a task runs

1. The dataset's books are copied into a workspace of the task's own, so tasks never
   affect one another or the dataset.
2. Two actors are registered there: `agent`, the agent under evaluation, holding
   READ_ONLY and PROPOSER, as a normal accounting agent does; and `clerk`, a human
   colleague.
3. The task is prepared: its instructions and expected answer are derived from the
   books and the ground truth, and whatever it needs is set up through the workflow
   by the clerk, such as a bill to record or an entry to check. Choices, such as which
   account to ask about, come from a random stream seeded by the dataset's seed and
   the task's id, so a task is the same every time for a dataset.
4. The agent is given the task's prompt and the tools, and returns its answer.
5. The answer is scored, and the metrics counted, against the books as the agent left
   them.

## Tasks

| Id | Task | Expected answer | Scored by |
| --- | --- | --- | --- |
| GL-001 | Get an account balance | The kernel's balance of one account at a month end, signed: positive for a debit balance, negative for a credit balance | Exact amount |
| GL-002 | Calculate the trial balance | Every account's signed balance and their total, which is zero, at a month end | F1 of the lines, total included |
| GL-003 | Find unusual transactions | The unusual charges injected | F1 of the entry ids |
| GL-004 | Identify incorrectly classified expenses | The misposted entries, and the right account for each | F1 of (entry, account); classification |
| GL-005 | Identify wrong department and location tags | The mistagged entries, the dimension, and the right value | F1; classification |
| GL-006 | Identify entries in the wrong period | The misdated bills, and the period each belongs to | F1; classification |
| AP-001 | Find duplicate vendor invoices | The later copy of each duplicated bill | F1 of the object ids |
| AP-002 | Identify bills missing their vendor | The bills without a vendor, and the vendor of each | F1; classification |
| AP-003 | Find duplicate vendor payments | The second payment of each bill paid twice | F1 of the object ids |
| AP-004 | Identify bills recorded at the wrong amount | The entries whose amount differs from the bill, and the right amount | F1; classification; numerical |
| AR-001 | Identify outstanding customer invoices | The invoices no recorded payment settles, and their total | Half F1 of the invoices, half the exact total |
| AR-002 | Find customer receipts missing from the books | The bank statement lines of unrecorded payments | F1 of the object ids |
| CLOSE-001 | Identify missing accruals | The months whose legal fees were not accrued | F1 of the periods |
| JE-001 | Generate a journal entry | The entry for a new bill, as the company posts that vendor's bills | Line F1; workflow |
| JE-002 | Validate a journal entry | The issue codes of a colleague's invalid entry | F1 of the codes; workflow |
| JE-003 | Correct an invalid journal entry | The invalid entry voided and a valid one proposed | Line F1; workflow |

The F1 score of a set of items found is 1 when the answer names exactly the expected
items, and falls equally for each one missed and each false alarm; an empty answer to
an empty expectation scores 1. Amounts are compared exactly, to the cent.

For the journal entry tasks the answer is the id of the entry the agent proposed; the
entry itself is read from the books. It scores 0 unless the agent proposed it and it
records the task's bill; otherwise its score is the F1 of its lines (account, signed
amount, and dimensions) against the expected lines. Its workflow part is 1 when the
entry is proposed or submitted, valid, and, in JE-003, the invalid entry is voided.
JE-002's workflow part is 1 when the agent validated the entry through the workflow.

## Metrics

| Metric | What it is |
| --- | --- |
| `accounting_correctness` | The mean task score |
| `numerical_correctness` | The mean numerical part of the tasks that have one (GL-001, GL-002, AP-004, AR-001) |
| `classification_accuracy` | Of the answer's items that named a real error, the share that also gave the right correction |
| `workflow_accuracy` | The mean workflow part of the journal entry tasks |
| `tool_use_accuracy` | The share of tool calls neither rejected (no such tool, or arguments the tool's schema refuses) nor refused for naming a record that does not exist |
| `hallucination_rate` | The share of records the answers name (entries, objects, accounts, vendors, invoices, periods) that the books do not hold |
| `invalid_posting_rate` | The share of the agent's attempts to change the books that the workflow or the kernel refused |
| `auditability` | The share of the agent's changes recorded in the audit log with a reason and at least one evidence reference |

A metric no task measured, such as auditability for an agent that changed nothing, is
`null` rather than a score it did not earn.

## Agents

An agent is any object with a `name` and a `run` method:

```python
from opensumma.benchmark import TaskPrompt, Tools


class MyAgent:
    name = "my-agent"

    def run(self, task: TaskPrompt, tools: Tools) -> dict:
        # task.instructions says what to do; task.answer_schema is the JSON schema
        # of the answer. tools.specs lists the tools and their argument schemas.
        balance = tools.call("get_account_balance", code="6100", as_of="2026-06-30")
        if not balance.ok:
            return {}  # balance.error says what refused the call
        return {"balance": balance.result["balance"]}
```

`tools.call` returns a `ToolCall` with the structured `result`, or the `error` that
refused it: the same JSON the audit log records for a refusal, or `ToolCallRejected`
for a call no tool accepted. Tools are the MCP interface's, served in-process, so an
agent sees the same schemas, permissions, and refusals as one connected to
`python -m opensumma.mcp`. An answer is JSON values; amounts are strings. An agent
that raises, or returns something that is not JSON, scores 0 on that task, and the
error is recorded.

Two agents come built in. `null` answers nothing. `oracle` runs each task's reference
solution, which knows the expected answer and reaches it through the tools: it is a
calibration that shows every task can be solved and scored in full, not an agent
being evaluated, and the only one given the answer key.

The example agents of `opensumma.agents` are named too: `investigator`,
`journal-entry`, and `duplicate-invoice`, and `examples`, the three together. They
are real agents, not calibrations: they apply accounting rules to the books through
the tools and never see the answer key. [agents.md](agents.md) describes them and
how they score.

```bash
erp benchmark run --agent examples
erp benchmark run --agent journal-entry --tasks JE-001,JE-002,JE-003
```

## Results

A run's directory holds:

- `results.json`: the agent, the dataset (company, seed, fingerprint), the summary,
  and every task's instructions, expected answer, answer, score, and usage counts;
- `results.csv`: one row per task;
- `trajectories/<task>.jsonl`: one line per tool call (step, tool, arguments, result
  or error), then a line with the answer and any error;
- `workspaces/<task>/books.db`: the books as the agent left them, with its audit
  log.

Nothing in the results depends on when the run happened, so a deterministic agent
gets byte-identical results from every run, which the tests check. Trajectories
record each tool result exactly as the agent saw it, so they also hold when records
were written (`created_at`, `updated_at`, `posted_at`); for the records a task sets
up during the run, such as the clerk's bill in JE-001 and JE-003, that is the time
of the run. Apart from those fields, a deterministic agent's trajectories are the
same from run to run too.

# OpenSumma

An open-source accounting environment for building and evaluating AI accounting agents.

OpenSumma provides a deterministic double-entry accounting kernel that AI agents interact
with through semantic tools and workflows. The core rule:

> **AI proposes and reasons. The accounting kernel validates and records.**

The project is under active, phased development. See [progress.md](progress.md) for the
current state and [docs/roadmap.md](docs/roadmap.md) for the plan.

## Development setup

Requires Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Common commands

```bash
pytest                   # run the test suite
ruff check .             # lint
ruff format .            # format
mypy                     # type-check
alembic upgrade head     # create/upgrade the database schema
erp dataset generate --company acme --transactions 1000 --seed 42  # a dataset
```

The database URL is read from `OPENSUMMA_DATABASE_URL` and defaults to
`sqlite:///opensumma.db` in the current directory. The schema can also be created from
Python:

```python
from opensumma.db import init_db

init_db("sqlite:///opensumma.db")
```

## Using the kernel

The accounting kernel is usable directly from Python, with no server running. It
covers the chart of accounts, accounting periods, dimensions, journal entries, the
ledger they post to, and the financial reports derived from it.

```python
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import (
    JournalEntry,
    JournalEntryError,
    LineInput,
    balance_sheet,
    create_calendar_year_periods,
    create_journal_entry,
    income_statement,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    seed_dimensions,
    trial_balance,
    validate_journal_entry,
)

url = "sqlite:///opensumma.db"
init_db(url)

with Session(create_engine(url)) as session:
    seed_chart_of_accounts(session)
    seed_dimensions(session)
    create_calendar_year_periods(session, 2026)
    session.commit()

    # Record drafts, then validate and post them.
    capital = create_journal_entry(
        session,
        entry_date=date(2026, 3, 1),
        description="Owner investment",
        lines=[
            LineInput("1111", debit=Decimal("10000.00")),
            LineInput("3100", credit=Decimal("10000.00")),
        ],
    )
    aws = create_journal_entry(
        session,
        entry_date=date(2026, 3, 15),
        description="AWS invoice for March, unpaid",
        lines=[
            LineInput(
                "6100", debit=Decimal("120.50"), dimensions={"DEPARTMENT": "ENG"}
            ),
            LineInput("2110", credit=Decimal("120.50")),
        ],
    )
    print(validate_journal_entry(session, aws))  # []
    for entry in (capital, aws):
        post_journal_entry(session, entry)
    session.commit()

    # Validation reports every problem at once, with stable codes.
    draft = create_journal_entry(
        session,
        entry_date=date(2026, 3, 15),
        description="Misposted",
        lines=[
            LineInput("6000", debit=Decimal("10.00")),  # a parent account
            LineInput("1111", credit=Decimal("9.99")),  # does not balance
        ],
    )
    try:
        post_journal_entry(session, draft)
    except JournalEntryError as error:
        print([issue.code.value for issue in error.issues])
        # ['UNBALANCED', 'ACCOUNT_NOT_POSTABLE']

    # Reports derive from the posted ledger only; the draft above is not in it.
    march_end = date(2026, 3, 31)
    trial = trial_balance(session, as_of=march_end)
    print(trial.total_debits, trial.total_credits)  # 10120.50 10120.50
    march = income_statement(session, start=date(2026, 3, 1), end=march_end)
    print(march.net_income)  # -120.50
    sheet = balance_sheet(session, as_of=march_end)
    print(sheet.assets.total, sheet.total_liabilities_and_equity)  # 10000.00 10000.00

    # A posted entry is never edited; it is reversed by a new entry.
    reversal = reverse_journal_entry(session, aws, entry_date=march_end)
    session.commit()
    print(aws.status.value, reversal.status.value)  # REVERSED POSTED
```

## Accounting Objects

Business documents such as vendor bills, invoices, and payments are Accounting
Objects (`opensumma.objects`). They name the vendor or customer they concern, carry
business context as JSON and a history of business events, and reach the ledger only
through journal entries that the kernel validates and posts. Continuing the example
above:

```python
from datetime import UTC, datetime

from opensumma.kernel import get_journal_entry
from opensumma.objects import (
    ObjectHasAccountingImpactError,
    accounting_impact,
    create_accounting_object,
    create_journal_entry_for_object,
    record_accounting_event,
    search_accounting_objects,
    seed_counterparties,
    void_accounting_object,
)

with Session(create_engine(url)) as session:
    seed_counterparties(session)  # six vendors and five customers

    # A bill arrives. Business data is JSON; amounts are strings, never floats.
    bill = create_accounting_object(
        session,
        object_type="vendor_bill",
        occurred_at=datetime(2026, 3, 20, 9, 30, tzinfo=UTC),
        source="email",
        counterparty="V-STRATUS",
        data={"invoice_number": "INV-2002", "amount": "80.00"},
    )
    record_accounting_event(
        session,
        bill,
        event_type="approved_for_payment",
        occurred_at=datetime(2026, 3, 21, 14, 0, tzinfo=UTC),
        source="email",
    )

    # Its accounting impact is a journal entry, validated and posted by the kernel.
    entry = create_journal_entry_for_object(
        session,
        bill,
        entry_date=date(2026, 3, 20),
        description="Stratus INV-2002",
        lines=[
            LineInput("5200", debit=Decimal("80.00")),
            LineInput("2110", credit=Decimal("80.00")),
        ],
    )
    post_journal_entry(session, entry)
    session.commit()
    print(accounting_impact(session, bill).has_net_impact)  # True

    # The same bill arrives again; its business data gives it away.
    duplicate = create_accounting_object(
        session,
        object_type="vendor_bill",
        occurred_at=datetime(2026, 3, 22, 8, 0, tzinfo=UTC),
        source="vendor_portal",
        counterparty="V-STRATUS",
        data={"invoice_number": "INV-2002", "amount": "80.00"},
    )
    matches = search_accounting_objects(
        session, counterparty="V-STRATUS", data={"invoice_number": "INV-2002"}
    )
    print(len(matches))  # 2
    void_accounting_object(session, duplicate)  # it never reached the ledger

    # An object the ledger still carries cannot be voided away; reverse it first.
    try:
        void_accounting_object(session, bill)
    except ObjectHasAccountingImpactError as error:
        print(error.entry_ids == (entry.id,))  # True
    reverse_journal_entry(session, entry, entry_date=date(2026, 3, 31))
    void_accounting_object(session, bill)
    session.commit()
    print(bill.status.value, get_journal_entry(session, entry.id).status.value)
    # VOIDED REVERSED
```

## The workflow

Actors (people, AI agents, and system processes) move entries and objects through
the workflow (`opensumma.workflow`), each step needing an explicit permission and
each recorded. A normal agent may read and propose, but not approve or post, and no
one approves an entry they prepared:

```python
from opensumma import workflow
from opensumma.workflow import ActorType, Permission

with Session(create_engine(url)) as session:
    agent = workflow.create_actor(
        session,
        code="ap-agent",
        name="AP agent",
        actor_type=ActorType.AGENT,
        permissions=[Permission.READ_ONLY, Permission.PROPOSER],
    )
    maria = workflow.create_actor(
        session,
        code="maria",
        name="Maria Chen",
        actor_type=ActorType.HUMAN,
        permissions=[Permission.APPROVER],
    )
    poster = workflow.create_actor(
        session,
        code="posting-service",
        name="Posting service",
        actor_type=ActorType.SYSTEM,
        permissions=[Permission.POSTER],
    )

    # The agent turns an emailed bill into a proposal ...
    bill = workflow.observe_accounting_object(
        session,
        actor=agent,
        object_type="vendor_bill",
        occurred_at=datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
        source="email",
        data={"raw": "Paper Trail Office Supply / INV-5 / 45.00"},
    )
    workflow.extract_accounting_object(
        session, bill, actor=agent, data={"invoice_number": "INV-5", "amount": "45.00"}
    )
    workflow.classify_accounting_object(
        session, bill, actor=agent, counterparty="V-PAPER"
    )
    entry = workflow.propose_journal_entry(
        session,
        actor=agent,
        entry_date=date(2026, 3, 25),
        description="Paper Trail INV-5",
        lines=[
            LineInput("6700", debit=Decimal("45.00")),
            LineInput("2110", credit=Decimal("45.00")),
        ],
        accounting_object=bill,
    )
    workflow.submit_for_approval(session, entry, actor=agent)

    # ... which it cannot approve itself.
    try:
        workflow.approve_journal_entry(session, entry, actor=agent)
    except workflow.PermissionDeniedError as error:
        print(error)  # actor ap-agent (AGENT) lacks the APPROVER permission

    workflow.approve_journal_entry(session, entry, actor=maria, reason="Matches PO")
    workflow.post_journal_entry(session, entry, actor=poster)
    session.commit()
    history = workflow.workflow_history(session, entry)
    print([(step.action.value, step.actor.code) for step in history])
    # [('propose', 'ap-agent'), ('submit', 'ap-agent'), ('approve', 'maria'),
    #  ('post', 'posting-service')]
```

The kernel's and object layer's own operations stay available to trusted Python code;
every interface an actor can reach goes through the workflow.

## The audit log

Every workflow action, allowed or refused, is an audit event naming its actor, input,
output, concise reason, and evidence references. Changes made outside the workflow
are captured as the system's. The log is hash-chained, so it can be verified.
Continuing the example above:

```python
from opensumma.workflow import audit_history, verify_audit_log

with Session(create_engine(url)) as session:
    entry = session.get(JournalEntry, entry.id)
    for event in audit_history(session, subject=entry):
        actor = event.actor.code if event.actor else "system"
        print(event.action, actor, event.result.value)
    # propose_journal_entry ap-agent SUCCEEDED
    # submit_for_approval ap-agent SUCCEEDED
    # approve_journal_entry ap-agent REFUSED
    # approve_journal_entry maria SUCCEEDED
    # post_journal_entry posting-service SUCCEEDED

    refused = audit_history(session, subject=entry, result="REFUSED")[0]
    print(refused.output["error"], refused.output["permission"])
    # PermissionDeniedError APPROVER

    print(verify_audit_log(session).is_intact)  # True
```

## The REST API

The same operations are served over HTTP (`opensumma.api`, built on FastAPI). A
caller identifies itself with an actor's API key, which is shown once when issued
and stored only as a hash. Continuing the examples above:

```python
from opensumma.workflow import get_actor, issue_api_key

with Session(create_engine(url)) as session:
    key = issue_api_key(session, get_actor(session, "ap-agent"))
    session.commit()
    print(key.startswith("osk_"))  # True; keep the key, it is not shown again
```

Then serve the API and call it as that actor:

```bash
python -m opensumma.api --database-url sqlite:///opensumma.db  # docs at /docs
export KEY=osk_...                                             # the key above

curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/accounts/6100
# {"code":"6100","name":"Software Subscriptions","account_type":"EXPENSE",...}

curl -s -X POST http://127.0.0.1:8000/journal-entries \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"entry_date": "2026-03-28", "description": "Brightline, March",
       "lines": [{"account": "6100", "debit": "49.00"},
                 {"account": "2110", "credit": "49.00"}]}'
# {"id":8,"entry_date":"2026-03-28","description":"Brightline, March","status":"PROPOSED",...}

curl -s -X POST -H "Authorization: Bearer $KEY" \
  http://127.0.0.1:8000/journal-entries/8/approve
# {"error":"InvalidTransitionError","message":"cannot approve from PROPOSED; ...}
```

Amounts are strings, never JSON numbers. Every read needs READ_ONLY; every change is
a workflow operation, with its permission checks and its audit event. A refusal is
answered with the same JSON the audit log records for it.

## The MCP server

The same operations are MCP tools (`opensumma.mcp`), so an agent's host can offer
them to a model directly: 15 read-only tools, such as `get_trial_balance`, and 14
mutating ones, each the workflow operation of its name, such as
`propose_journal_entry`. A server acts as one actor, whose API key it reads from
`OPENSUMMA_API_KEY`, and speaks MCP on stdio. A host launches it from its
configuration, for example:

```json
{
  "mcpServers": {
    "opensumma": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "opensumma.mcp", "--database-url", "sqlite:////path/to/opensumma.db"],
      "env": {"OPENSUMMA_API_KEY": "osk_..."}
    }
  }
}
```

The MCP SDK's client can drive it from Python too. Continuing the examples above,
with the agent's key:

```python
import asyncio
import sys

from mcp import Client, StdioServerParameters


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opensumma.mcp", "--database-url", url],
        env={"OPENSUMMA_API_KEY": key},
    )
    async with Client(server) as client:
        balance = await client.call_tool(
            "get_account_balance", {"code": "6700", "as_of": "2026-03-31"}
        )
        print(balance.structured_content["balance"])  # 45.00

        entry = await client.call_tool(
            "propose_journal_entry",
            {
                "entry_date": "2026-03-29",
                "description": "Paper Trail INV-6",
                "lines": [
                    {"account": "6700", "debit": "30.00"},
                    {"account": "2110", "credit": "30.00"},
                ],
                "reason": "Same vendor and account as INV-5",
                "evidence": ["invoice=INV-6"],
            },
        )
        entry_id = entry.structured_content["id"]
        await client.call_tool("submit_for_approval", {"entry_id": entry_id})

        refused = await client.call_tool(
            "approve_journal_entry", {"entry_id": entry_id}
        )
        print(refused.is_error, refused.structured_content["permission"])
        # True APPROVER


asyncio.run(main())
```

Every read needs READ_ONLY and every change is audited, as on the REST API. A
refusal is an error result carrying the same JSON the audit log records for it, and
an argument a tool does not declare is refused rather than ignored.

## Datasets

Agents are evaluated on generated companies: a year of books with known errors in
them, and the ground truth of every error. The same seed always gives the same
dataset.

```bash
erp dataset generate --company acme --transactions 10000 --seed 42
# Generated acme: 10,000 transactions in 2026, 10,013 journal entries in the books, 100 injected errors.
#   books:        datasets/acme/books.db
#   manifest:     datasets/acme/manifest.json
#   ground truth: datasets/acme/ground_truth.json
#   fingerprint:  sha256:...
```

`books.db` is the company's books, which the REST and MCP interfaces serve as they
are; give an agent a copy of it and nothing else. `ground_truth.json` lists each
error: its type (a duplicate invoice, a wrong account, a missing accrual, and eight
more), the entries as recorded and as they should be, and how far each account is
off. The same from Python:

```python
from opensumma.datasets import write_dataset

dataset = write_dataset("datasets/demo", company="demo", transactions=1000, seed=42)
print(dataset.manifest["year_end"]["is_balanced"])  # True
first = dataset.ground_truth["errors"][0]
print(first["type"], first["journal_entry_ids"])  # missing_vendor [263]
print(first["details"]["vendor"])  # V-STRATUS
```

[docs/datasets.md](docs/datasets.md) describes the business, the error types, and
the ground truth format.

## Documentation

- [docs/architecture.md](docs/architecture.md): layers, boundaries, and infrastructure decisions
- [docs/accounting-model.md](docs/accounting-model.md): accounting invariants and domain model
- [docs/agent-model.md](docs/agent-model.md): how AI agents interact with the kernel
- [docs/datasets.md](docs/datasets.md): generated datasets, their errors, and their ground truth
- [docs/roadmap.md](docs/roadmap.md): phases and acceptance criteria

# Architecture

OpenSumma is an AI accounting laboratory: a deterministic double-entry accounting kernel,
semantic interfaces through which AI agents use it, and reproducible datasets and
benchmarks for evaluating those agents. It is not a general-purpose CRUD ERP.

The governing rule:

> **AI proposes and reasons. The accounting kernel validates and records.**

The LLM is never the system of record; the double-entry ledger is. Agents act only through
semantic tools and workflows and never get unrestricted SQL access.

## Layers

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

Dependency rules:

- Dependencies point downward only. In packages: `kernel` ← `objects` ← `workflow` ←
  `interface` ← `api` and `mcp`. The Accounting Object layer sits above the kernel;
  the workflow engine above both; the REST and MCP interfaces above everything, side
  by side, sharing `interface`, which presents records and commits actions for both.
  The dataset generator, `datasets`, is trusted code above the workflow and beside
  the interfaces. The benchmark, `benchmark`, sits on top of the datasets and the MCP
  interface, through whose tools its agents act. The example agents, `agents`, sit
  on top of the benchmark and see only what it hands an agent: the task's prompt
  and the tools. The `erp` command line (`cli.py`) drives the generator and the
  benchmark.
  `tests/unit/test_layering.py` fails if a layer imports one above it, if any domain
  layer or the generator imports FastAPI, Starlette, uvicorn, Pydantic, or MCP, if
  `interface` imports either interface or its framework, if either interface
  imports the other or its framework, if anything but the command line and the
  benchmark imports the generator, or anything but the command line and the example
  agents the benchmark, or if an example agent imports anything but the benchmark's
  `TaskPrompt`, `Tools`, and `ToolCall`, the other agents, and the parts of the
  standard library that compute. That is what guarantees no report can read an
  Accounting Object, the domain never depends on an interface, and the example
  agents reach the books only through the tools.
- The diagram draws the kernel and the workflow engine side by side because they are
  peers in purpose: one decides what is valid, the other who may act and when. In
  code the workflow wraps the kernel, so it sits above it.
- The domain services, accounting kernel, and workflow engine never import FastAPI or MCP.
- The accounting kernel is usable directly from Python, with no server running.
- REST and MCP are thin adapters over the same application API: the workflow's
  operations and the kernel's reads, presented through `opensumma.interface`.
  Neither contains accounting logic, and both are subject to the same validation,
  permissions, and audit.
- Every interface an actor can reach goes through the workflow, where actors and
  permissions exist. The kernel and object layer beneath stay unguarded for trusted
  code, such as the dataset generator, which must be able to create the mistakes the
  workflow's controls exist to catch. The ledger's own invariants hold on every path.

## Three distinct models

| Model | What it is | Example |
| --- | --- | --- |
| Persistence model | Relational SQLAlchemy tables | `journal_line` table |
| Domain model | Semantic accounting objects | `JournalEntry`, `AccountingPeriod` |
| Agent interface | Semantic tools | `get_trial_balance`, `propose_journal_entry` |

Persistence details (table names, keys, SQL) do not leak into the agent interface.

## Package layout

Current state (Phase 12):

```text
src/opensumma/
    db.py               declarative Base, TimestampMixin, enum columns, engines and
                        their URLs, init_db(), and each backend's own SQL
    money.py            Money column type (integer cents), round_money(), ensure_money()
    utc.py              UtcDateTime column type, ensure_utc(), ensure_date(), utcnow()
    kernel/             the accounting kernel
        enums.py            account types, normal balances, statuses, issue codes
        errors.py           the accounting rules the kernel can reject
        models.py           the tables, and the session hooks guarding recorded entries
        accounts.py         chart-of-accounts services
        periods.py          accounting period services, and holding a period to post
        dimensions.py       dimension services
        journal.py          journal entries: record, validate, post, void, reverse
        ledger.py           the ledger: posted lines, activity, and account balances
        reports.py          trial balance, general ledger, income statement, balance sheet
        seed.py             default chart of accounts and dimensions
    objects/            Accounting Objects, above the kernel
        enums.py            object types, stages, and counterparty kinds
        errors.py           the rules the object layer can reject
        data.py             BusinessData JSON column type, ensure_business_data()
        models.py           counterparties, objects, business events, links to journal
                            entries, and the session hooks guarding their history
        counterparties.py   vendors and customers, and the default cast
        services.py         record, search, and void objects; record events; link
                            entries; derive accounting impact
    workflow/           the workflow engine, above the kernel and the object layer
        enums.py            actor types, permissions, workflow actions, audit results
        errors.py           the rules the workflow can reject
        models.py           actors, their permissions, the workflow history, and the
                            hash-chained audit log, with the guards keeping both
                            append-only
        audit.py            recording audited actions, capturing changes made outside
                            them, reading the log, and verifying its chain
        actors.py           actor services, API keys, and the permission check
        machine.py          the transition tables, holding a subject, and recording
                            transitions
        entries.py          propose, validate, submit, approve, reject, post, reverse,
                            and void journal entries
        accounting_objects.py  observe, extract, classify, and void objects
        periods.py          close and reopen periods, in order
    interface/          what the REST and MCP interfaces share
        schemas.py          request and response models (Pydantic)
        views.py            domain records as the interfaces present them
        work.py             the unit of work of one action: commit, keeping refusals'
                            audit events
    api/                the REST interface (FastAPI)
        app.py              create_app(): routers, error handlers, /health
        dependencies.py     the request's session and calling actor
        errors.py           refusals as HTTP responses
        master_data.py      accounts, balances, periods, dimensions, counterparties
        journal.py          journal entries through the workflow, and the ledger
        reports.py          trial balance, income statement, balance sheet, general
                            ledger
        objects.py          accounting objects through the workflow
        audit.py            the audit log, page by page
        __main__.py         python -m opensumma.api
    mcp/                the MCP interface (MCP Python SDK)
        server.py           create_server(): the tools, their annotations, refusals
        context.py          the call's session and calling actor
        read_tools.py       the read-only tools
        mutating_tools.py   the mutating tools, one per workflow operation
        __main__.py         python -m opensumma.mcp, on stdio
    datasets/           the deterministic dataset generator, trusted code above the
                        workflow
        rng.py              random draws that are the same on every platform
        model.py            a plan: transactions, documents, statement lines
        cast.py             the vendors, customers, products, and card merchants
        business.py         planning a year of business, exactly N transactions
        errors.py           injecting known errors, and their ground truth
        recorder.py         recording a plan through the kernel and object layer
        books.py            the books' canonical content, and its fingerprint
        generator.py        plan_dataset, generate_dataset, write_dataset
    benchmark/          the benchmark: tasks, agents, scoring, and results
        schema.py           the task schema: Task, TaskPrompt, Instance, Score
        tasks.py            the sixteen tasks, their scoring and reference solutions
        environment.py      loading a dataset; a task's workspace and actors
        tools.py            the MCP tools for an agent, recording its trajectory
        agents.py           the Agent protocol, the null and oracle agents, and the
                            example agents' names
        runner.py           running tasks, the metrics, and exporting results
    agents/             example agents, on top of the benchmark, using its tools alone
        common.py           calling tools, reading the books through them, findings
        investigation.py    the investigation agent and its rules
        journal_entries.py  the journal entry agent: record, check, and correct entries
        duplicates.py       the duplicate-invoice agent
        team.py             the three as one agent
    cli.py              the erp command line: dataset generate, benchmark run
    __main__.py         python -m opensumma, the same command line
    migrations/         Alembic environment and revisions, shipped inside the package
tests/
    unit/           pure logic, no database
    integration/    database and migration behaviour
    acceptance/     phase acceptance criteria, exercised through the public API
docs/
```

The top-level modules are persistence primitives that every layer above may use.
`kernel/` is the accounting domain: it depends on those primitives and on nothing
above itself. Reports read posted data only through `ledger.py`, which is what makes
"reports derive from the ledger" a property of the code rather than a convention.

`opensumma.kernel` re-exports everything a caller needs, so importing it is both the
public API and what registers the persistence models on `Base.metadata`.
`opensumma.objects` and `opensumma.workflow` do the same for their layers, each
importing the layers beneath it.

`objects/` depends on the kernel's public API and on nothing above itself. It links
objects to journal entries from its own table, so the kernel's tables are unchanged
and the kernel works without it. An object's accounting impact is read through
`ledger.py` like everything else derived from the ledger.

`workflow/` wraps the kernel and object operations: its operations carry the agent
tools' names and check state, permission, and controls before calling the operation
of the same name beneath. Each runs inside `audited`, which records it in the audit
log. The audit log lives in this package rather than one of its own because its
events name actors and the workflow's operations write them; a separate package
would depend on the workflow and be depended on by it. The transition tables in `machine.py` are data, so the
rules about when an action is allowed are in one place and can be read and tested as
a table.

`interface/` is what the REST and MCP interfaces share, so that an account, an
entry, or a refusal looks the same on either: the response models, the views that
build them from domain records, and the unit of work that commits an action, or
only its audit event if a rule refused it. It depends on Pydantic, but on neither
interface's framework, and neither interface depends on the other.

### Protection below the services

The kernel's services are the intended way to change accounting data, but the rules
that must never break are enforced a level lower, where every caller passes. The
database holds row-level rules as constraints. `kernel/models.py` registers hooks on
SQLAlchemy's `Session` class, so they apply to every session in the process:

- `before_flush` refuses any change to a posted, reversed, or voided journal entry,
  and any change to the content of one awaiting approval or approved, judged against
  the status recorded in the database. It refuses to let an unbalanced entry, or one
  with fewer than two lines, into the ledger.
- `do_orm_execute` refuses bulk INSERT, UPDATE, and DELETE on journal tables, which
  would otherwise bypass the flush hook.

Details are in [accounting-model.md](accounting-model.md#immutability).

`objects/models.py` guards object history in the same way, on every session:
objects are never deleted, business events and links to journal entries are never
changed or deleted, a voided object cannot change or gain entries, and bulk writes to
the object tables are refused. The `BusinessData` column type refuses floats on every
write, bulk statements included. Details are in
[accounting-model.md](accounting-model.md#accounting-objects-phase-4).

`workflow/models.py` keeps the workflow history and the audit log append-only the same
way: no transition or audit event is changed or deleted, and bulk writes to them are
refused. A `before_flush` hook numbers each new audit event and chains it to the one
before by hash, however it was created, so tampering beyond the ORM is detectable by
`verify_audit_log`.

`workflow/audit.py` captures every change made outside an audited action from
SQLAlchemy's mapper events (`after_insert`, `before_update`, `before_delete`), which
fire for every row the flush writes, orphans deleted by a cascade included, and in
the order it writes them. Previous values are read from the database on the flush's
connection, because attribute history forgets them once a commit has expired an
object. The events are added in `after_flush_postexec` and written by the same
commit. Bulk INSERT, UPDATE, and DELETE through the session are refused on every
table, since they would escape the capture; raw SQL remains the boundary.

Modules for later layers are added in the phase that needs them (see
[roadmap.md](roadmap.md)). There are no empty placeholder packages.

## REST interface

Implemented in `opensumma.api` (Phase 7). It is a thin adapter: reads call the
kernel and object layer, and every change is a workflow operation, so it holds no
accounting logic of its own.

- **Identity.** A caller sends an actor's API key as `Authorization: Bearer <key>`.
  Keys are issued with `issue_api_key` and stored only as SHA-256 hashes, in the
  workflow layer; `revoke_api_keys` revokes them. Trusting a header that merely names
  an actor would let an agent claim to be its approver.
- **Permissions.** Every read requires READ_ONLY. Every change requires whatever the
  workflow operation requires, and is audited by it.
- **One unit of work per request.** A request gets its own session. An action that
  succeeds is committed; one a rule refuses is committed too, which persists only its
  audit event, since a refusal changes nothing else; any other failure rolls back.
  The unit of work is `interface/work.py`, which the MCP interface uses too.
- **Refusals.** Every refusal is answered with the same JSON the audit log records:
  the error, its message, and what it names (issue codes, a missing permission, the
  states an action is allowed from). Statuses follow the error: 401 no valid key, 403
  not permitted, 404 unknown record, 409 not allowed in the current state, 422
  content or input the rules reject. A request that does not match the schema gets a
  422 `RequestValidationError` and never reaches the workflow, so it is not audited.
- **Money as text.** Amounts are JSON strings, such as `"120.50"`, in both
  directions. A JSON number is refused as an amount, so a float cannot enter through
  the interface. A journal line carries one signed `amount`, a debit positive and a
  credit negative (`"-120.50"`), and balances are signed the same way; only the
  financial statements state amounts in each section's normal direction. Requests
  refuse fields the schema does not name, so a line written with the former `debit`
  and `credit` fields is refused rather than recorded without an amount.
- **Identifiers.** Accounts are addressed by their code (`/accounts/6100`), periods by
  their code (`/periods/2026-03`), and entries, objects, and audit events by number.
- **Running it.** `python -m opensumma.api [--host H] [--port P] [--database-url U]`,
  or `uvicorn --factory opensumma.api:create_app`. The schema must already be current;
  `/health`, which needs no key, answers 503 if it is not. OpenAPI documentation is
  served at `/docs`.

## MCP interface

Implemented in `opensumma.mcp` (Phase 8) with the MCP Python SDK (2.x, whose server
class is `MCPServer`). Like the REST interface it is a thin adapter: the read-only
tools call the kernel and object layer, and each mutating tool is the workflow
operation of the same name. The two interfaces serve the same operations, with the
same identity, permissions, audit, and JSON.

- **Identity.** A server acts as one actor: the one whose API key it was started
  with, from `OPENSUMMA_API_KEY`. The key is read from the environment, where MCP
  hosts put a server's credentials, rather than the command line, where the process
  list would show it. It is checked on every call, so revoking it, or deactivating
  the actor, cuts a running server off at once. Several actors means several
  servers, one per actor, over the same database.
- **Read-only and mutating tools are kept apart:** in their own modules
  (`read_tools.py`, `mutating_tools.py`), and on the wire, where each tool is
  annotated `readOnlyHint` true or false, so a host can let an agent read freely and
  confirm each change. Both sets are closed-world (`openWorldHint` false). Every
  tool is listed to every actor; a tool the actor lacks the permission for is
  refused, and the attempt is audited, which is what a benchmark needs in order to
  count invalid postings.
- **Permissions.** Every read tool requires READ_ONLY, and is not audited. Every
  mutating tool requires whatever its workflow operation requires, and is audited by
  it.
- **One unit of work per call.** Each call opens its own session and commits through
  `interface/work.py`, exactly as a REST request does.
- **Refusals.** A refused call returns an error result (`isError`) whose structured
  content is the JSON the audit log records for it: the error, its message, and what
  it names (issue codes, a missing permission, the states an action is allowed
  from). There are no status codes; the `error` field names the kind. Arguments that
  do not match a tool's schema are refused by the SDK, as text naming each field,
  before the tool runs, so they are not audited.
- **Money as text, and no invented arguments.** Amounts are strings, as on the REST
  interface, signed the same way, and a JSON number is refused as an amount. The
  tools' descriptions state the sign convention, since an agent reads them to decide
  how to call a tool. Every tool's input schema
  has `additionalProperties: false`, and an argument a tool does not declare is
  refused. The SDK ignores unknown arguments by default, which would let an argument
  an agent invented, such as `approved`, silently do nothing; the server replaces
  each tool's argument model with one that forbids them.
- **Results.** A result is one JSON document, both as structured content and as a
  single text block. A list is returned in a named field, such as
  `{"accounts": [...]}`, because the SDK would otherwise send a list as one text
  block per item, and an empty list as no content at all.
- **Tools beyond the specification's list,** as on the REST interface:
  `reject_journal_entry` and `void_journal_entry`; `observe_accounting_object`,
  `extract_accounting_object`, `classify_accounting_object`, and
  `void_accounting_object`; `close_period` and `reopen_period`; and reads for
  periods, dimensions, counterparties, the income statement, the balance sheet, and
  raw ledger lines (`get_ledger`). `search_accounting_objects` also takes a `data`
  filter, which the REST endpoint does not.
- **Running it.** `python -m opensumma.mcp [--database-url U]` serves the tools on
  stdio, which is how MCP hosts launch local servers; it will not start without a
  key. The schema must already be current. The server sends instructions at
  initialization: amounts as strings, the entry lifecycle, and concise reasons and
  evidence. There is no HTTP transport: it would need per-request identity, which
  the SDK provides only as OAuth.

## Datasets

Implemented in `opensumma.datasets` (Phase 9), and described fully in
[datasets.md](datasets.md). `erp dataset generate --company acme --transactions
10000 --seed 42` writes a company's books (`books.db`), a manifest, and the ground
truth of the errors injected into them.

- **Plan, then record.** The generator first plans the year as plain values, with no
  database: a fixed monthly schedule and a variable business that together make
  exactly the transactions asked for. Errors are then injected into the plan, which
  records each one's entries as they are and as they should be. Only then is the
  plan recorded, so bad parameters are refused before any file is written, and the
  plan can be tested quickly on its own.
- **Deterministic.** Every draw comes from `random.random()`, in a stream per purpose
  seeded by the seed and the purpose's name, so the same parameters give the same
  books on every platform and Python version, and the business beneath the errors
  is the same with or without them. The manifest's fingerprint hashes the books'
  content, leaving out when rows were written.
- **Trusted, beneath the workflow.** The recorder calls the kernel and the object
  layer directly, since the workflow would refuse the errors it must create. Every
  entry is still validated and posted by the kernel, and every flush passes the
  ledger's guards. The whole run is one audited `generate_dataset` action. Autoflush
  is suspended and the session flushed every 500 records, which halves the time the
  hooks would otherwise take.

## Benchmark

Implemented in `opensumma.benchmark` (Phase 10), and described fully in
[benchmark.md](benchmark.md). `erp benchmark run` runs an agent on sixteen tasks
against a generated dataset and writes scores, metrics, and trajectories.

- **One workspace per task.** Each task runs on its own copy of the dataset's books,
  with the agent and a clerk registered on it, so tasks are independent and the
  agent's changes, and their audit log, stay behind for inspection.
- **Agents act through the MCP tools.** The runner serves the MCP server in-process,
  as the agent's actor, and `Tools` presents it synchronously and records every call.
  An agent is scored on the same interface any MCP client has, with the same
  permissions, refusals, and audit, and never touches the database.
- **Deterministic tasks and scores.** Expected answers come from the books and the
  ground truth, never from a model. Answers are Pydantic models, which also give each
  task's JSON schema. Sets of items are scored by F1, amounts exactly, entries line by
  line. The metrics are counted from the answer, the trajectory, and the workspace's
  audit log.
- **An oracle for calibration.** Each task carries a reference solution that reaches
  the expected answer through the tools; the `oracle` agent runs them, showing every
  task can be solved and scored in full.
- **Reproducible results.** Nothing in the results depends on when a run happened,
  so a deterministic agent's results are byte-identical from run to run.
  Trajectories keep each tool result as the agent saw it, so the times the task's
  own records were written show in them.

## Example agents

Implemented in `opensumma.agents` (Phase 11), and described fully in
[agents.md](agents.md): an investigation agent, a journal entry agent, and a
duplicate-invoice agent, and the three as one.

- **Built on the benchmark's agent interface.** Each is an `Agent`, with a `name`
  and a `run` that gets the task's prompt and the tools. The benchmark names them,
  so `--agent examples` loads them, but never imports them, since they are built on
  it.
- **The tools are their only way to the books.** They may import only the
  benchmark's `TaskPrompt`, `Tools`, and `ToolCall`, one another, and the parts of
  the standard library that compute, which the layering test enforces. They act as
  any MCP client would, with the same permissions, refusals, and audit.
- **Rules, not a model.** Each workflow applies explicit accounting rules to what
  the books hold, so the agents are deterministic baselines. `Books` reads the
  books through the tools once per task and indexes them, so a workflow's checks
  share its reads.
- **Auditable.** Every change is a workflow tool call with a concise reason and
  evidence references. Findings are reported beside the answer with theirs, in a
  field the benchmark records but does not score.

## Database

- All persistence goes through SQLAlchemy 2.x with portable types and expressions, so
  the same code runs on SQLite and PostgreSQL (Phase 12). Database-specific SQL is
  used only when unavoidable, and is kept in `opensumma.db`: SQLite's foreign-key
  pragma and PostgreSQL's advisory lock (see [Concurrency](#concurrency)).
- SQLite is the default. The URL comes from `OPENSUMMA_DATABASE_URL` and defaults to
  `sqlite:///opensumma.db`.
- PostgreSQL needs the `postgresql` extra (`pip install "opensumma[postgresql]"`),
  which installs psycopg 3. A URL that names no driver, such as
  `postgresql://ledger:secret@db/books`, connects with it (`engine_url`), rather than
  with SQLAlchemy's default of psycopg2; `postgres://` means the same. The database
  must exist; `init_db()` or `alembic upgrade head` creates the schema in it.
- `opensumma.db.create_engine()` turns on `PRAGMA foreign_keys=ON` for every SQLite
  connection. SQLite otherwise ignores foreign keys, and referential integrity is not
  optional for a ledger. PostgreSQL always enforces them.
- Monetary columns use `opensumma.money.Money`: exact signed integer cents, never
  floats. A journal line's one `amount` column is positive for a debit and negative for
  a credit. See [accounting-model.md](accounting-model.md#money).
- Business data uses `opensumma.objects.data.BusinessData`, a portable `JSON` column
  that refuses floats. Its fields are queried with SQLAlchemy's JSON operators, which
  render for SQLite and PostgreSQL alike.
- Timestamp columns use `opensumma.utc.UtcDateTime`: timezone-aware UTC in and out, on
  every backend.
- Enum columns are portable text with an explicit CHECK constraint rather than a native
  database enum, because PostgreSQL enum types are awkward to alter and SQLite has no
  equivalent. The CHECK is declared in `__table_args__` instead of being generated by
  `sa.Enum(create_constraint=True)`: the type emits the constraint itself *and* Alembic
  renders it again, which produces several constraints sharing one name. SQLite tolerates
  that; PostgreSQL would reject it.
- `Base.metadata` carries a constraint naming convention. Deterministic names keep
  autogenerated migrations reproducible and let SQLite batch migrations find constraints.
- What differs between the backends stays beneath the types: PostgreSQL's `SUM` of
  integer cents is a `numeric`, which `Money` reads back exactly as it reads an
  integer, and psycopg hands JSON back decoded where SQLite hands back its text, which
  only raw SQL ever sees.
- The same seed generates the same books on both: the dataset generator, writing
  into a PostgreSQL session, gives the fingerprint, ids, and ground truth it gives on
  SQLite, which the Phase 12 acceptance test checks.

## Concurrency

SQLite lets one transaction write at a time, so the books never see two writers
act at once. PostgreSQL runs transactions side by side, and any rule checked in
Python against what a transaction read, and acted on later, could be broken by
another transaction in between. Three rules need more than their check:

- **The audit log is one chain.** Each event is numbered and hashed after the last
  one recorded, so transactions must append in turn. A PostgreSQL row lock cannot
  stand for the end of a table, so the hook that chains events first takes a
  transaction-level advisory lock (`opensumma.db.serialize`, the one piece of
  PostgreSQL-specific SQL); another appender waits until the first commits, then
  reads the event it added.
- **A workflow action judges the state as it is.** Every operation on an existing
  entry, object, or period holds it first (`machine.hold`): `SELECT ... FOR UPDATE`
  on its row, re-reading its status. Of two approvals, or postings, of one entry at
  once, the second waits for the first and is then refused, as it would be one after
  the other.
- **A closed period receives nothing.** Posting, and posting a reversal, takes a
  shared lock on the period the entry falls in (`hold_period_for`), and closing
  holds the period exclusively, so a close and a posting into that period happen one
  after the other. Postings into one period do not wait for one another.

`with_for_update` is SQLAlchemy's portable form of these row locks; on SQLite it
renders nothing, since SQLite has no row locks. SQLite books are therefore for one
writer at a time, as they always were: several processes writing one SQLite file
at once are not protected, and PostgreSQL is the backend for concurrent use. Beyond
these rules, a race ends in a database refusal rather than inconsistent books: a
second reversal of one entry violates the unique `reversal_of_id`, and a deadlock
between two transactions is broken by PostgreSQL rolling one back.

`tests/integration/test_concurrency.py` makes each of these races happen on
PostgreSQL, deterministically: the first transaction acts, a second acts on the same
record in another thread while the first is still open, and the first then commits.

## Migrations

- Alembic migrations are the single source of truth for the schema. Application code
  creates schemas only through `opensumma.db.init_db()` or `alembic upgrade head`.
- Migrations live in `src/opensumma/migrations` so that an installed package can create
  its own databases, which dataset and benchmark environments will need.
- There are two configurations with the same settings: `alembic.ini` for the command
  line, and `opensumma.db.alembic_config()` for code.
- `render_as_batch=True`: on SQLite, Alembic rebuilds tables to apply changes SQLite
  cannot `ALTER`; on other databases it emits ordinary `ALTER` statements.
- Foreign-key enforcement is off while migrating, because batch mode drops and recreates
  tables.
- Generated revisions are formatted and linted by Ruff through post-write hooks.
- Offline SQL generation (`alembic upgrade head --sql`) works for PostgreSQL, where a
  DBA would use it, and a test keeps it working. It does not work for SQLite past
  revision `5db61b0df835`: that revision adds a constraint to an existing table, which
  SQLite can only do by rebuilding the table, and batch mode must read the live table to
  rebuild it. SQLite databases are always migrated live, through `init_db()` or
  `alembic upgrade head`.
- Revision `db546c4d06cf` replaced each journal line's `debit` and `credit` columns
  with one signed `amount`, converting every row as `debit - credit` in one SQL
  statement. Its downgrade splits the amounts back, and refuses books that hold a zero
  line, which the earlier schema could not represent, rather than altering posted
  lines.

Workflow for schema changes:

1. Change or add a persistence model. A new model module must be reachable from
   `opensumma.workflow`, which `migrations/env.py` imports (it imports the layers
   beneath it in turn), or autogenerate will not see it. A new application column type needs a
   rendering rule in `env.py` too, so migrations record the physical type.
2. `alembic revision --autogenerate -m "describe the change"`
3. Review the generated revision. Autogenerate is a starting point, not a guarantee:
   it does not see CHECK constraints, and it drops columns rather than converting
   their data. Write conversions as SQL statements, so offline SQL generation
   includes them, and test a rebuilt table's CHECK constraints directly, as
   `test_rebuilding_a_table_keeps_its_check_constraints` does.
4. Run `pytest`. `test_models_match_migrations` fails if models and migrations disagree.

## Tooling

- pytest, with warnings treated as errors. For example, SQLAlchemy's warning about
  converting `Decimal` to float fails the suite instead of passing silently.
- Hypothesis for property-based tests of the ledger's invariants
  (`tests/integration/test_ledger_properties.py`), a development dependency only.
- Most integration tests run on an in-memory SQLite database, because every DDL
  statement on a file waits for the disk. Migration and acceptance tests use a real
  file, created through Alembic.
- The same tests run on PostgreSQL when `OPENSUMMA_TEST_POSTGRESQL_URL` names a server
  whose user may create databases, such as
  `postgresql://postgres:secret@localhost:5432/postgres`. Every fixture that gives a
  test a database (`session`, `engine`, `database_url`, `module_engine`, and the REST
  and MCP books) then creates it on that server instead, copying a template database
  where the SQLite fixtures copy a file, and drops it afterwards. Tests marked
  `postgresql`, such as the concurrency tests and the Phase 12 acceptance tests, are
  skipped without a server. Datasets, the benchmark, and the example agents keep to
  SQLite files, which is what a dataset is.
- Ruff for linting and formatting; mypy in strict mode.
- GitHub Actions (`.github/workflows/ci.yml`) on Python 3.12 and 3.13 with SQLite, and
  on Python 3.12 against a PostgreSQL 16 service with the `postgresql` extra. CI
  installs the package non-editably, so tests run against the built package.

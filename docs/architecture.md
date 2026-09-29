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

- Dependencies point downward only. In packages: `kernel` ← `objects` ← `workflow`.
  The Accounting Object layer sits above the kernel and depends on it; the workflow
  engine sits above both. `tests/unit/test_layering.py` fails if the kernel imports
  either layer above it, or the object layer imports the workflow. That is what
  guarantees no report can read an Accounting Object.
- The diagram draws the kernel and the workflow engine side by side because they are
  peers in purpose: one decides what is valid, the other who may act and when. In
  code the workflow wraps the kernel, so it sits above it.
- The domain services, accounting kernel, and workflow engine never import FastAPI or MCP.
- The accounting kernel is usable directly from Python, with no server running.
- REST and MCP are thin adapters over the same application API. Neither contains
  accounting logic, and both are subject to the same validation.
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

Current state (Phase 6):

```text
src/opensumma/
    db.py               declarative Base, TimestampMixin, enum columns, engine, init_db()
    money.py            Money column type (integer cents), round_money(), ensure_money()
    utc.py              UtcDateTime column type, ensure_utc(), ensure_date(), utcnow()
    kernel/             the accounting kernel
        enums.py            account types, normal balances, statuses, issue codes
        errors.py           the accounting rules the kernel can reject
        models.py           the tables, and the session hooks guarding recorded entries
        accounts.py         chart-of-accounts services
        periods.py          accounting period services
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
        actors.py           actor services and the permission check
        machine.py          the transition tables, and recording transitions
        entries.py          propose, validate, submit, approve, reject, post, reverse,
                            and void journal entries
        accounting_objects.py  observe, extract, classify, and void objects
        periods.py          close and reopen periods, in order
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

## Database

- All persistence goes through SQLAlchemy 2.x with portable types and expressions.
  Database-specific SQL is used only when unavoidable, and is kept isolated.
- SQLite is the default. The URL comes from `OPENSUMMA_DATABASE_URL` and defaults to
  `sqlite:///opensumma.db`.
- `opensumma.db.create_engine()` turns on `PRAGMA foreign_keys=ON` for every SQLite
  connection. SQLite otherwise ignores foreign keys, and referential integrity is not
  optional for a ledger.
- Monetary columns use `opensumma.money.Money`: exact integer cents, never floats. See
  [accounting-model.md](accounting-model.md#money).
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
- Ruff for linting and formatting; mypy in strict mode.
- GitHub Actions (`.github/workflows/ci.yml`) on Python 3.12 and 3.13. CI installs the
  package non-editably, so tests run against the built package.

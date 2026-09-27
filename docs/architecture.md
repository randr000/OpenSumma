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

- Dependencies point downward only.
- The domain services, accounting kernel, and workflow engine never import FastAPI or MCP.
- The accounting kernel is usable directly from Python, with no server running.
- REST and MCP are thin adapters over the same application API. Neither contains
  accounting logic, and both are subject to the same validation.

## Three distinct models

| Model | What it is | Example |
| --- | --- | --- |
| Persistence model | Relational SQLAlchemy tables | `journal_line` table |
| Domain model | Semantic accounting objects | `JournalEntry`, `AccountingPeriod` |
| Agent interface | Semantic tools | `get_trial_balance`, `propose_journal_entry` |

Persistence details (table names, keys, SQL) do not leak into the agent interface.

## Package layout

Current state (Phase 2):

```text
src/opensumma/
    db.py               declarative Base, TimestampMixin, engine creation, init_db()
    money.py            Money column type (integer cents), round_money(), ensure_money()
    utc.py              UtcDateTime column type and utcnow()
    kernel/             the accounting kernel
        enums.py            account types, normal balances, statuses, issue codes
        errors.py           the accounting rules the kernel can reject
        models.py           the tables, and the session hooks guarding recorded entries
        accounts.py         chart-of-accounts services
        periods.py          accounting period services
        dimensions.py       dimension services
        journal.py          journal entries: record, validate, post, void, reverse
        seed.py             default chart of accounts and dimensions
    migrations/         Alembic environment and revisions, shipped inside the package
tests/
    unit/           pure logic, no database
    integration/    database and migration behaviour
    acceptance/     phase acceptance criteria, exercised through the public API
docs/
```

The top-level modules are persistence primitives that every layer above may use.
`kernel/` is the accounting domain: it depends on those primitives and on nothing
above itself. The ledger and financial reports join it in Phase 3.

`opensumma.kernel` re-exports everything a caller needs, so importing it is both the
public API and what registers the persistence models on `Base.metadata`.

### Protection below the services

The kernel's services are the intended way to change accounting data, but the rules
that must never break are enforced a level lower, where every caller passes. The
database holds row-level rules as constraints. `kernel/models.py` registers hooks on
SQLAlchemy's `Session` class, so they apply to every session in the process:

- `before_flush` refuses any change to a posted, reversed, or voided journal entry,
  judged against the status recorded in the database, and refuses to let an
  unbalanced entry, or one with fewer than two lines, into the ledger.
- `do_orm_execute` refuses bulk INSERT, UPDATE, and DELETE on journal tables, which
  would otherwise bypass the flush hook.

Details are in [accounting-model.md](accounting-model.md#immutability).

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

Workflow for schema changes:

1. Change or add a persistence model. A new model module must be reachable from
   `opensumma.kernel`, which `migrations/env.py` imports, or autogenerate will not see it.
2. `alembic revision --autogenerate -m "describe the change"`
3. Review the generated revision. Autogenerate is a starting point, not a guarantee.
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

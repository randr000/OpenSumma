"""add counterparties, actors, and workflow history

Phase 5: vendors and customers become master data, and accounting objects name
their counterparty through ``counterparty_id`` instead of the free-text
``entity_id``, which read as if it named a business entity. Actors, their
permissions, and the append-only history of workflow transitions are added, and
accounting objects gain the EXTRACTED and CLASSIFIED stages.

A database that already holds objects with an ``entity_id`` keeps that information:
each distinct value becomes a counterparty with the same code and name. Its kind is
inferred from the objects that named it: a customer if they were only customer
invoices, customer payments, or sales orders, and a vendor otherwise. Converted
counterparties are worth reviewing. Downgrading copies the codes back.

Revision ID: 71801d14980d
Revises: de5016324ad2
Create Date: 2026-09-29 00:15:32.315623

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "71801d14980d"
down_revision: str | Sequence[str] | None = "de5016324ad2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CUSTOMER_TYPES = ("customer_invoice", "customer_payment", "sales_order")
_VENDOR_TYPES = ("vendor_bill", "vendor_payment", "purchase_order")

_objects = sa.table(
    "accounting_object",
    sa.column("object_type", sa.String()),
    sa.column("status", sa.String()),
    sa.column("entity_id", sa.String()),
    sa.column("counterparty_id", sa.Integer()),
)
_counterparty = sa.table(
    "counterparty",
    sa.column("id", sa.Integer()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("kind", sa.String()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _status_type(*values: str) -> sa.Enum:
    return sa.Enum(*values, name="accountingobjectstatus", native_enum=False)


def _status_check(*values: str) -> str:
    return "status IN ({})".format(", ".join(f"'{value}'" for value in values))


_OLD_STATUSES = ("OBSERVED", "VOIDED")
_NEW_STATUSES = ("OBSERVED", "EXTRACTED", "CLASSIFIED", "VOIDED")


def upgrade() -> None:
    op.create_table(
        "actor",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "actor_type",
            sa.Enum("HUMAN", "AGENT", "SYSTEM", name="actortype", native_enum=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('HUMAN', 'AGENT', 'SYSTEM')",
            name=op.f("ck_actor_actor_type_is_valid"),
        ),
        sa.CheckConstraint("length(code) > 0", name=op.f("ck_actor_code_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actor")),
        sa.UniqueConstraint("code", name=op.f("uq_actor_code")),
    )
    op.create_table(
        "counterparty",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("VENDOR", "CUSTOMER", name="counterpartykind", native_enum=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('VENDOR', 'CUSTOMER')", name=op.f("ck_counterparty_kind_is_valid")
        ),
        sa.CheckConstraint(
            "length(code) > 0", name=op.f("ck_counterparty_code_not_empty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_counterparty")),
        sa.UniqueConstraint("code", name=op.f("uq_counterparty_code")),
    )
    op.create_table(
        "actor_permission",
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column(
            "permission",
            sa.Enum(
                "READ_ONLY",
                "PROPOSER",
                "APPROVER",
                "POSTER",
                "ADMIN",
                name="permission",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "permission IN ('READ_ONLY', 'PROPOSER', 'APPROVER', 'POSTER', 'ADMIN')",
            name=op.f("ck_actor_permission_permission_is_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_actor_permission_actor_id_actor")
        ),
        sa.PrimaryKeyConstraint(
            "actor_id", "permission", name=op.f("pk_actor_permission")
        ),
    )
    op.create_table(
        "workflow_transition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=True),
        sa.Column("accounting_object_id", sa.Integer(), nullable=True),
        sa.Column("accounting_period_id", sa.Integer(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "observe",
                "extract",
                "classify",
                "propose",
                "submit",
                "approve",
                "reject",
                "post",
                "reverse",
                "void",
                "close",
                "reopen",
                name="workflowaction",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('observe', 'extract', 'classify', 'propose', 'submit', "
            "'approve', 'reject', 'post', 'reverse', 'void', 'close', 'reopen')",
            name=op.f("ck_workflow_transition_action_is_valid"),
        ),
        sa.CheckConstraint(
            "(CASE WHEN journal_entry_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN accounting_object_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN accounting_period_id IS NULL THEN 0 ELSE 1 END) = 1",
            name=op.f("ck_workflow_transition_one_subject"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(reason) > 0",
            name=op.f("ck_workflow_transition_reason_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["accounting_object_id"],
            ["accounting_object.id"],
            name=op.f("fk_workflow_transition_accounting_object_id_accounting_object"),
        ),
        sa.ForeignKeyConstraint(
            ["accounting_period_id"],
            ["accounting_period.id"],
            name=op.f("fk_workflow_transition_accounting_period_id_accounting_period"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["actor.id"],
            name=op.f("fk_workflow_transition_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entry.id"],
            name=op.f("fk_workflow_transition_journal_entry_id_journal_entry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_transition")),
    )
    with op.batch_alter_table("workflow_transition", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_workflow_transition_accounting_object_id"),
            ["accounting_object_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_workflow_transition_accounting_period_id"),
            ["accounting_period_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_workflow_transition_actor_id"), ["actor_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_workflow_transition_journal_entry_id"),
            ["journal_entry_id"],
            unique=False,
        )

    with op.batch_alter_table("accounting_object", schema=None) as batch_op:
        batch_op.add_column(sa.Column("counterparty_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_accounting_object_counterparty_id"),
            ["counterparty_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_accounting_object_counterparty_id_counterparty"),
            "counterparty",
            ["counterparty_id"],
            ["id"],
        )

    _convert_entity_ids_to_counterparties()

    with op.batch_alter_table("accounting_object", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounting_object_entity_id"))
        batch_op.drop_constraint(
            batch_op.f("ck_accounting_object_entity_id_not_empty"), type_="check"
        )
        batch_op.drop_column("entity_id")
        batch_op.drop_constraint(
            batch_op.f("ck_accounting_object_status_is_valid"), type_="check"
        )
        batch_op.alter_column(
            "status",
            existing_type=_status_type(*_OLD_STATUSES),
            type_=_status_type(*_NEW_STATUSES),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_accounting_object_status_is_valid"),
            _status_check(*_NEW_STATUSES),
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_transition", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflow_transition_journal_entry_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_transition_actor_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_transition_accounting_period_id"))
        batch_op.drop_index(batch_op.f("ix_workflow_transition_accounting_object_id"))

    op.drop_table("workflow_transition")
    op.drop_table("actor_permission")
    op.drop_table("actor")

    with op.batch_alter_table("accounting_object", schema=None) as batch_op:
        batch_op.add_column(sa.Column("entity_id", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_accounting_object_entity_id"), ["entity_id"], unique=False
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_accounting_object_entity_id_not_empty"),
            "entity_id IS NULL OR length(entity_id) > 0",
        )

    op.execute(
        _objects.update()
        .where(_objects.c.counterparty_id.is_not(None))
        .values(
            entity_id=sa.select(_counterparty.c.code)
            .where(_counterparty.c.id == _objects.c.counterparty_id)
            .scalar_subquery()
        )
    )
    # The earlier schema knows only these two stages.
    op.execute(
        _objects.update()
        .where(_objects.c.status.in_(("EXTRACTED", "CLASSIFIED")))
        .values(status="OBSERVED")
    )

    with op.batch_alter_table("accounting_object", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_accounting_object_counterparty_id_counterparty"),
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_accounting_object_counterparty_id"))
        batch_op.drop_column("counterparty_id")
        batch_op.drop_constraint(
            batch_op.f("ck_accounting_object_status_is_valid"), type_="check"
        )
        batch_op.alter_column(
            "status",
            existing_type=_status_type(*_NEW_STATUSES),
            type_=_status_type(*_OLD_STATUSES),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_accounting_object_status_is_valid"),
            _status_check(*_OLD_STATUSES),
        )

    op.drop_table("counterparty")


def _convert_entity_ids_to_counterparties() -> None:
    """Turn each distinct entity_id into a counterparty and point objects at it.

    Written as SQL statements rather than Python loops, so offline SQL generation
    (``alembic upgrade --sql``) produces them too.
    """
    names_customers = sa.func.max(
        sa.case((_objects.c.object_type.in_(_CUSTOMER_TYPES), 1), else_=0)
    )
    names_vendors = sa.func.max(
        sa.case((_objects.c.object_type.in_(_VENDOR_TYPES), 1), else_=0)
    )
    kind = sa.case(
        (sa.and_(names_customers == 1, names_vendors == 0), "CUSTOMER"),
        else_="VENDOR",
    )
    now = sa.func.current_timestamp()
    op.execute(
        _counterparty.insert().from_select(
            ["code", "name", "kind", "is_active", "created_at", "updated_at"],
            sa.select(
                _objects.c.entity_id, _objects.c.entity_id, kind, sa.true(), now, now
            )
            .where(_objects.c.entity_id.is_not(None))
            .group_by(_objects.c.entity_id),
        )
    )
    op.execute(
        _objects.update()
        .where(_objects.c.entity_id.is_not(None))
        .values(
            counterparty_id=sa.select(_counterparty.c.id)
            .where(_counterparty.c.code == _objects.c.entity_id)
            .scalar_subquery()
        )
    )

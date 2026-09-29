"""add accounting objects

Phase 4: accounting objects with JSON business data, the business events that
happen to them, and the links to the journal entries that record their accounting
impact. The links refer to journal entries, never the other way round, so the
kernel's tables are unchanged.

Revision ID: de5016324ad2
Revises: 75bb6e001f07
Create Date: 2026-09-28 23:42:01.451636

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de5016324ad2"
down_revision: str | Sequence[str] | None = "75bb6e001f07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounting_object",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "object_type",
            sa.Enum(
                "vendor_bill",
                "customer_invoice",
                "customer_payment",
                "vendor_payment",
                "bank_transaction",
                "expense",
                "purchase_order",
                "sales_order",
                "contract",
                "journal_entry",
                "reconciliation",
                name="accountingobjecttype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OBSERVED", "VOIDED", name="accountingobjectstatus", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('vendor_bill', 'customer_invoice', 'customer_payment', "
            "'vendor_payment', 'bank_transaction', 'expense', 'purchase_order', "
            "'sales_order', 'contract', 'journal_entry', 'reconciliation')",
            name=op.f("ck_accounting_object_object_type_is_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('OBSERVED', 'VOIDED')",
            name=op.f("ck_accounting_object_status_is_valid"),
        ),
        sa.CheckConstraint(
            "entity_id IS NULL OR length(entity_id) > 0",
            name=op.f("ck_accounting_object_entity_id_not_empty"),
        ),
        sa.CheckConstraint(
            "length(source) > 0", name=op.f("ck_accounting_object_source_not_empty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounting_object")),
    )
    with op.batch_alter_table("accounting_object", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_accounting_object_entity_id"), ["entity_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_accounting_object_object_type"),
            ["object_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_accounting_object_occurred_at"),
            ["occurred_at"],
            unique=False,
        )

    op.create_table(
        "accounting_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accounting_object_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(event_type) > 0",
            name=op.f("ck_accounting_event_event_type_not_empty"),
        ),
        sa.CheckConstraint(
            "length(source) > 0", name=op.f("ck_accounting_event_source_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["accounting_object_id"],
            ["accounting_object.id"],
            name=op.f("fk_accounting_event_accounting_object_id_accounting_object"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounting_event")),
    )
    with op.batch_alter_table("accounting_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_accounting_event_accounting_object_id"),
            ["accounting_object_id"],
            unique=False,
        )

    op.create_table(
        "accounting_object_entry",
        sa.Column("accounting_object_id", sa.Integer(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["accounting_object_id"],
            ["accounting_object.id"],
            name=op.f(
                "fk_accounting_object_entry_accounting_object_id_accounting_object"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entry.id"],
            name=op.f("fk_accounting_object_entry_journal_entry_id_journal_entry"),
        ),
        sa.PrimaryKeyConstraint(
            "accounting_object_id",
            "journal_entry_id",
            name=op.f("pk_accounting_object_entry"),
        ),
    )
    with op.batch_alter_table("accounting_object_entry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_accounting_object_entry_journal_entry_id"),
            ["journal_entry_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("accounting_object_entry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounting_object_entry_journal_entry_id"))

    op.drop_table("accounting_object_entry")
    with op.batch_alter_table("accounting_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounting_event_accounting_object_id"))

    op.drop_table("accounting_event")
    with op.batch_alter_table("accounting_object", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounting_object_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_accounting_object_object_type"))
        batch_op.drop_index(batch_op.f("ix_accounting_object_entity_id"))

    op.drop_table("accounting_object")

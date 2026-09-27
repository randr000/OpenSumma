"""add chart of accounts, accounting periods, and dimensions

Phase 1 master data. Enum columns are portable text with a CHECK constraint
rather than a native database enum, and timestamps are UTC.

Revision ID: 7ad156fb7f04
Revises: 8b22e47e3e66
Create Date: 2026-09-27 15:54:51.585270

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ad156fb7f04"
down_revision: str | Sequence[str] | None = "8b22e47e3e66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "account_type",
            sa.Enum(
                "ASSET",
                "LIABILITY",
                "EQUITY",
                "REVENUE",
                "EXPENSE",
                name="accounttype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "normal_balance",
            sa.Enum("DEBIT", "CREDIT", name="normalbalance", native_enum=False),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')",
            name=op.f("ck_account_account_type_is_valid"),
        ),
        sa.CheckConstraint(
            "normal_balance IN ('DEBIT', 'CREDIT')",
            name=op.f("ck_account_normal_balance_is_valid"),
        ),
        sa.CheckConstraint("length(code) > 0", name=op.f("ck_account_code_not_empty")),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name=op.f("ck_account_parent_is_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["account.id"], name=op.f("fk_account_parent_id_account")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
        sa.UniqueConstraint("code", name=op.f("uq_account_code")),
    )
    op.create_table(
        "accounting_period",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "CLOSED", name="periodstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CLOSED')",
            name=op.f("ck_accounting_period_status_is_valid"),
        ),
        sa.CheckConstraint(
            "end_date >= start_date",
            name=op.f("ck_accounting_period_end_not_before_start"),
        ),
        sa.CheckConstraint(
            "length(code) > 0", name=op.f("ck_accounting_period_code_not_empty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounting_period")),
        sa.UniqueConstraint("code", name=op.f("uq_accounting_period_code")),
    )
    op.create_table(
        "dimension",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(code) > 0", name=op.f("ck_dimension_code_not_empty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dimension")),
        sa.UniqueConstraint("code", name=op.f("uq_dimension_code")),
    )
    op.create_table(
        "dimension_value",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dimension_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(code) > 0", name=op.f("ck_dimension_value_code_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"],
            ["dimension.id"],
            name=op.f("fk_dimension_value_dimension_id_dimension"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dimension_value")),
        sa.UniqueConstraint(
            "dimension_id", "code", name=op.f("uq_dimension_value_dimension_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("dimension_value")
    op.drop_table("dimension")
    op.drop_table("accounting_period")
    op.drop_table("account")

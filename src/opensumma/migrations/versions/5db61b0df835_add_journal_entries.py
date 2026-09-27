"""add journal entries

Phase 2: journal entries, their lines, and the dimension values on each line.

A line's dimension value is referenced together with its dimension, which needs
a unique key on dimension_value (id, dimension_id). That key is created before
the table that references it and dropped after it: PostgreSQL refuses a foreign
key whose target columns are not unique.

Revision ID: 5db61b0df835
Revises: 7ad156fb7f04
Create Date: 2026-09-27 16:27:55.497085

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5db61b0df835"
down_revision: str | Sequence[str] | None = "7ad156fb7f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PROPOSED",
                "PENDING_APPROVAL",
                "APPROVED",
                "POSTED",
                "REVERSED",
                "VOIDED",
                name="journalentrystatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status IN ('POSTED', 'REVERSED') AND posted_at IS NOT NULL) "
            "OR (status NOT IN ('POSTED', 'REVERSED') AND posted_at IS NULL)",
            name=op.f("ck_journal_entry_posted_at_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PROPOSED', 'PENDING_APPROVAL', 'APPROVED', "
            "'POSTED', 'REVERSED', 'VOIDED')",
            name=op.f("ck_journal_entry_status_is_valid"),
        ),
        sa.CheckConstraint(
            "length(description) > 0",
            name=op.f("ck_journal_entry_description_not_empty"),
        ),
        sa.CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name=op.f("ck_journal_entry_does_not_reverse_itself"),
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_id"],
            ["journal_entry.id"],
            name=op.f("fk_journal_entry_reversal_of_id_journal_entry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entry")),
        sa.UniqueConstraint(
            "reversal_of_id", name=op.f("uq_journal_entry_reversal_of_id")
        ),
    )
    op.create_table(
        "journal_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("debit", sa.BigInteger(), nullable=False),
        sa.Column("credit", sa.BigInteger(), nullable=False),
        sa.Column("memo", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name=op.f("ck_journal_line_one_positive_side"),
        ),
        sa.CheckConstraint(
            "line_number >= 1", name=op.f("ck_journal_line_line_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_journal_line_account_id_account"),
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entry.id"],
            name=op.f("fk_journal_line_journal_entry_id_journal_entry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_line")),
        sa.UniqueConstraint(
            "journal_entry_id",
            "line_number",
            name=op.f("uq_journal_line_journal_entry_id"),
        ),
    )
    with op.batch_alter_table("journal_line", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_line_account_id"), ["account_id"], unique=False
        )

    with op.batch_alter_table("dimension_value", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            batch_op.f("uq_dimension_value_id"), ["id", "dimension_id"]
        )

    op.create_table(
        "journal_line_dimension",
        sa.Column("journal_line_id", sa.Integer(), nullable=False),
        sa.Column("dimension_id", sa.Integer(), nullable=False),
        sa.Column("dimension_value_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_value_id", "dimension_id"],
            ["dimension_value.id", "dimension_value.dimension_id"],
            name=op.f("fk_journal_line_dimension_dimension_value_id_dimension_value"),
        ),
        sa.ForeignKeyConstraint(
            ["journal_line_id"],
            ["journal_line.id"],
            name=op.f("fk_journal_line_dimension_journal_line_id_journal_line"),
        ),
        sa.PrimaryKeyConstraint(
            "journal_line_id", "dimension_id", name=op.f("pk_journal_line_dimension")
        ),
    )


def downgrade() -> None:
    op.drop_table("journal_line_dimension")
    with op.batch_alter_table("dimension_value", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_dimension_value_id"), type_="unique")

    with op.batch_alter_table("journal_line", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_journal_line_account_id"))

    op.drop_table("journal_line")
    op.drop_table("journal_entry")

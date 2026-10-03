"""store each journal line as one signed amount

Journal lines carried a debit and a credit, exactly one of them strictly positive.
They now carry one signed amount: a debit is positive, a credit negative, and a line
may be zero. Every existing line keeps its meaning, since its amount is its debit
less its credit.

Downgrading splits each amount back into a debit and a credit. The earlier schema
needs every line to have one strictly positive side, so it cannot hold a zero line:
a database with one is refused rather than changed, because posted lines are
permanent.

Revision ID: db546c4d06cf
Revises: dab3bc95c36e
Create Date: 2026-10-02 20:51:03.684589

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "db546c4d06cf"
down_revision: str | Sequence[str] | None = "dab3bc95c36e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_lines = sa.table(
    "journal_line",
    sa.column("debit", sa.BigInteger()),
    sa.column("credit", sa.BigInteger()),
    sa.column("amount", sa.BigInteger()),
)


def upgrade() -> None:
    with op.batch_alter_table("journal_line", schema=None) as batch_op:
        batch_op.add_column(sa.Column("amount", sa.BigInteger(), nullable=True))

    op.execute(_lines.update().values(amount=_lines.c.debit - _lines.c.credit))

    with op.batch_alter_table("journal_line", schema=None) as batch_op:
        batch_op.alter_column("amount", existing_type=sa.BigInteger(), nullable=False)
        batch_op.drop_constraint(
            batch_op.f("ck_journal_line_one_positive_side"), type_="check"
        )
        batch_op.drop_column("credit")
        batch_op.drop_column("debit")


def downgrade() -> None:
    if not context.is_offline_mode():
        zero_lines = op.get_bind().scalar(
            sa.select(sa.func.count()).select_from(_lines).where(_lines.c.amount == 0)
        )
        if zero_lines:
            raise RuntimeError(
                "the earlier schema cannot hold journal lines with a zero amount, "
                "since each of its lines is a strictly positive debit or credit, "
                f"and this database has {zero_lines}"
            )

    with op.batch_alter_table("journal_line", schema=None) as batch_op:
        batch_op.add_column(sa.Column("debit", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("credit", sa.BigInteger(), nullable=True))

    op.execute(
        _lines.update().values(
            debit=sa.case((_lines.c.amount > 0, _lines.c.amount), else_=0),
            credit=sa.case((_lines.c.amount < 0, -_lines.c.amount), else_=0),
        )
    )

    with op.batch_alter_table("journal_line", schema=None) as batch_op:
        batch_op.alter_column("debit", existing_type=sa.BigInteger(), nullable=False)
        batch_op.alter_column("credit", existing_type=sa.BigInteger(), nullable=False)
        batch_op.drop_column("amount")
        batch_op.create_check_constraint(
            batch_op.f("ck_journal_line_one_positive_side"),
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
        )

"""index journal entries by accounting date

Phase 3: every report selects posted entries by accounting date, so the ledger
is read by date range.

Revision ID: 75bb6e001f07
Revises: 5db61b0df835
Create Date: 2026-09-27 18:02:07.668072

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "75bb6e001f07"
down_revision: str | Sequence[str] | None = "5db61b0df835"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("journal_entry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_entry_entry_date"), ["entry_date"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("journal_entry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_journal_entry_entry_date"))

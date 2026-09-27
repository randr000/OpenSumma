"""baseline

Empty starting point of the migration history, so every database is
version-tracked from the moment it is created.

Revision ID: 8b22e47e3e66
Revises:
Create Date: 2026-09-27 14:31:14.459374

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "8b22e47e3e66"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

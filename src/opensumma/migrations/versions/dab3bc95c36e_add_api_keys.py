"""add api keys

Phase 7: API keys by which the REST interface knows which actor is calling. Only
each key's SHA-256 hash is stored.

Revision ID: dab3bc95c36e
Revises: e933f730d688
Create Date: 2026-09-29 02:47:00.499369

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dab3bc95c36e"
down_revision: str | Sequence[str] | None = "e933f730d688"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(key_hash) = 64", name=op.f("ck_api_key_key_hash_is_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_api_key_actor_id_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_key")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_key_key_hash")),
    )
    with op.batch_alter_table("api_key", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_api_key_actor_id"), ["actor_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("api_key", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_key_actor_id"))

    op.drop_table("api_key")

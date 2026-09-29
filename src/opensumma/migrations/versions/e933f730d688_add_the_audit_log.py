"""add the audit log

Phase 6: one row per audited action or captured change, with its actor, input,
output, result, concise reason, and evidence references. Events are numbered and
hash-chained, so tampering beyond the ORM breaks the chain.

Revision ID: e933f730d688
Revises: 71801d14980d
Create Date: 2026-09-29 01:04:43.056146

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e933f730d688"
down_revision: str | Sequence[str] | None = "71801d14980d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_type",
            sa.Enum("HUMAN", "AGENT", "SYSTEM", name="actortype", native_enum=False),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=True),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column(
            "result",
            sa.Enum("SUCCEEDED", "REFUSED", name="auditresult", native_enum=False),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "actor_id IS NOT NULL OR actor_type = 'SYSTEM'",
            name=op.f("ck_audit_event_attributed"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('HUMAN', 'AGENT', 'SYSTEM')",
            name=op.f("ck_audit_event_actor_type_is_valid"),
        ),
        sa.CheckConstraint(
            "result IN ('SUCCEEDED', 'REFUSED')",
            name=op.f("ck_audit_event_result_is_valid"),
        ),
        sa.CheckConstraint(
            "(sequence = 1 AND previous_hash IS NULL)"
            " OR (sequence > 1 AND previous_hash IS NOT NULL)",
            name=op.f("ck_audit_event_chained"),
        ),
        sa.CheckConstraint(
            "length(action) > 0", name=op.f("ck_audit_event_action_not_empty")
        ),
        sa.CheckConstraint(
            "length(hash) = 64", name=op.f("ck_audit_event_hash_is_sha256")
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(reason) > 0",
            name=op.f("ck_audit_event_reason_not_empty"),
        ),
        sa.CheckConstraint(
            "sequence >= 1", name=op.f("ck_audit_event_sequence_positive")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_audit_event_actor_id_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        sa.UniqueConstraint("sequence", name=op.f("uq_audit_event_sequence")),
    )
    with op.batch_alter_table("audit_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audit_event_actor_id"), ["actor_id"], unique=False
        )
        batch_op.create_index(
            "ix_audit_event_subject", ["object_type", "object_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_event", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_event_subject")
        batch_op.drop_index(batch_op.f("ix_audit_event_actor_id"))

    op.drop_table("audit_event")

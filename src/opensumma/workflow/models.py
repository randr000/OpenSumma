"""Relational tables for the workflow engine: actors, their permissions, and the
history of every workflow transition.

A transition names exactly one subject (a journal entry, an accounting object, or
an accounting period), the action taken, the states before and after, and the actor
who took it. The history is append-only: the end of this module guards it.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, event, inspect
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
    relationship,
)

from opensumma.db import Base, TimestampMixin, enum_check, enum_column
from opensumma.kernel.models import (
    DESCRIPTION_LENGTH,
    NAME_LENGTH,
    AccountingPeriod,
    JournalEntry,
)
from opensumma.objects.models import AccountingObject
from opensumma.utc import UtcDateTime, utcnow
from opensumma.workflow.enums import ActorType, Permission, WorkflowAction
from opensumma.workflow.errors import ImmutableHistoryError

ACTOR_CODE_LENGTH = 64
STATUS_LENGTH = 32


class Actor(TimestampMixin, Base):
    """A person, AI agent, or system process that acts through the workflow.

    Permissions are granted explicitly, one by one. An inactive actor may not act.
    """

    __tablename__ = "actor"
    __table_args__ = (
        CheckConstraint("length(code) > 0", name="code_not_empty"),
        enum_check("actor_type", ActorType),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(ACTOR_CODE_LENGTH), unique=True)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    is_active: Mapped[bool] = mapped_column(default=True)

    grants: Mapped[list["ActorPermission"]] = relationship(
        back_populates="actor",
        order_by="ActorPermission.permission",
        cascade="all, delete-orphan",
    )

    @property
    def permissions(self) -> frozenset[Permission]:
        return frozenset(grant.permission for grant in self.grants)

    def __repr__(self) -> str:
        return f"Actor(code={self.code!r}, actor_type={self.actor_type.value})"


class ActorPermission(Base):
    """One permission granted to one actor."""

    __tablename__ = "actor_permission"
    __table_args__ = (enum_check("permission", Permission),)

    actor_id: Mapped[int] = mapped_column(ForeignKey("actor.id"), primary_key=True)
    permission: Mapped[Permission] = mapped_column(
        enum_column(Permission), primary_key=True
    )

    actor: Mapped[Actor] = relationship(back_populates="grants")


class WorkflowTransition(Base):
    """One step in the life of a journal entry, accounting object, or period.

    ``from_status`` is empty when the step brought the subject into being. The
    history is append-only: a transition is never changed or deleted.
    """

    __tablename__ = "workflow_transition"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN journal_entry_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN accounting_object_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN accounting_period_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="one_subject",
        ),
        CheckConstraint(
            "reason IS NULL OR length(reason) > 0", name="reason_not_empty"
        ),
        enum_check("action", WorkflowAction),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entry.id"), index=True
    )
    accounting_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_object.id"), index=True
    )
    accounting_period_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_period.id"), index=True
    )
    action: Mapped[WorkflowAction] = mapped_column(enum_column(WorkflowAction))
    from_status: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    to_status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    actor_id: Mapped[int] = mapped_column(ForeignKey("actor.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String(DESCRIPTION_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    journal_entry: Mapped[JournalEntry | None] = relationship()
    accounting_object: Mapped[AccountingObject | None] = relationship()
    accounting_period: Mapped[AccountingPeriod | None] = relationship()
    actor: Mapped[Actor] = relationship()

    def __repr__(self) -> str:
        return (
            f"WorkflowTransition(action={self.action.value!r}, "
            f"{self.from_status} -> {self.to_status})"
        )


# --- Protection of the workflow history -----------------------------------------


def _is_changed(record: object) -> bool:
    state = inspect(record, raiseerr=True)
    return any(
        state.attrs[column.key].history.has_changes()
        for column in state.mapper.column_attrs
    )


@event.listens_for(Session, "before_flush")
def _guard_workflow_history(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    for record in session.deleted:
        if isinstance(record, WorkflowTransition):
            raise ImmutableHistoryError(
                f"workflow transition {record.id} cannot be deleted; the workflow "
                "history is append-only"
            )
    for record in session.dirty:
        if isinstance(record, WorkflowTransition) and _is_changed(record):
            raise ImmutableHistoryError(
                f"workflow transition {record.id} cannot be changed; the workflow "
                "history is append-only"
            )


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_history_changes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.name == WorkflowTransition.__tablename__:
        raise ImmutableHistoryError(
            "bulk writes to workflow_transition are refused; transitions are "
            "recorded by the workflow engine"
        )

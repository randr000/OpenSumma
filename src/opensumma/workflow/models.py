"""Relational tables for the workflow engine: actors, their permissions, the
history of every workflow transition, and the audit log.

A transition names exactly one subject (a journal entry, an accounting object, or
an accounting period), the action taken, the states before and after, and the actor
who took it. An audit event records any action, by whom, with what input, and what
came of it. Both histories are append-only, which the end of this module guards, and
audit events are hash-chained so that tampering beyond the ORM is detectable.
"""

import hashlib
import json
import weakref
from datetime import datetime
from itertools import count
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    event,
    inspect,
    select,
)
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
    relationship,
)

from opensumma.db import Base, TimestampMixin, enum_check, enum_column, serialize
from opensumma.kernel.models import (
    DESCRIPTION_LENGTH,
    NAME_LENGTH,
    AccountingPeriod,
    JournalEntry,
)
from opensumma.objects.data import BusinessData
from opensumma.objects.models import AccountingObject
from opensumma.utc import UtcDateTime, ensure_utc, utcnow
from opensumma.workflow.enums import ActorType, AuditResult, Permission, WorkflowAction
from opensumma.workflow.errors import ImmutableHistoryError

ACTOR_CODE_LENGTH = 64
STATUS_LENGTH = 32
ACTION_LENGTH = 64
OBJECT_TYPE_LENGTH = 64
HASH_LENGTH = 64  # a SHA-256 digest in hexadecimal
# The lock a transaction holds while it appends to the audit log.
AUDIT_LOG_LOCK = "opensumma.audit_log"


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


class ApiKey(Base):
    """A credential by which an interface knows which actor is calling.

    Only the SHA-256 hash of the key is stored; the key itself is shown once, when
    it is issued. A revoked key identifies no one.
    """

    __tablename__ = "api_key"
    __table_args__ = (
        CheckConstraint(f"length(key_hash) = {HASH_LENGTH}", name="key_hash_is_sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("actor.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    actor: Mapped[Actor] = relationship()

    def __repr__(self) -> str:
        return f"ApiKey(id={self.id!r}, actor_id={self.actor_id!r})"


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


class AuditEvent(Base):
    """One action, by one actor, and what came of it.

    ``action`` names what was attempted, such as ``propose_journal_entry``, and
    ``object_type`` and ``object_id`` what it concerned. ``input`` and ``output`` are
    JSON, with amounts as strings. ``reason`` is a concise explanation, never a
    model's chain of thought, and ``evidence`` a list of references such as
    ``"vendor_id=42"``.

    An event without an actor is the system itself: a change made by trusted code
    outside any audited action. Only SYSTEM events may lack an actor.

    Events are numbered from 1 and chained: each carries the SHA-256 hash of its own
    content and of the event before it, so an event changed or removed by any means,
    raw SQL included, breaks the chain (see ``verify_audit_log``).
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "(sequence = 1 AND previous_hash IS NULL)"
            " OR (sequence > 1 AND previous_hash IS NOT NULL)",
            name="chained",
        ),
        CheckConstraint(
            "actor_id IS NOT NULL OR actor_type = 'SYSTEM'", name="attributed"
        ),
        CheckConstraint("length(action) > 0", name="action_not_empty"),
        CheckConstraint(
            "reason IS NULL OR length(reason) > 0", name="reason_not_empty"
        ),
        CheckConstraint(f"length(hash) = {HASH_LENGTH}", name="hash_is_sha256"),
        enum_check("actor_type", ActorType),
        enum_check("result", AuditResult),
        # An object's audit trail is read by its type and id together.
        Index("ix_audit_event_subject", "object_type", "object_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(unique=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("actor.id"), index=True)
    action: Mapped[str] = mapped_column(String(ACTION_LENGTH))
    object_type: Mapped[str | None] = mapped_column(String(OBJECT_TYPE_LENGTH))
    object_id: Mapped[int | None]
    input: Mapped[dict[str, Any]] = mapped_column(BusinessData)
    output: Mapped[dict[str, Any]] = mapped_column(BusinessData)
    result: Mapped[AuditResult] = mapped_column(enum_column(AuditResult))
    reason: Mapped[str | None] = mapped_column(String(DESCRIPTION_LENGTH))
    evidence: Mapped[list[str]] = mapped_column(JSON)
    previous_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH))
    hash: Mapped[str] = mapped_column(String(HASH_LENGTH))

    actor: Mapped[Actor | None] = relationship()

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("occurred_at", utcnow())
        kwargs.setdefault("input", {})
        kwargs.setdefault("output", {})
        kwargs.setdefault("evidence", [])
        super().__init__(**kwargs)
        _creation_order[self] = next(_created)

    def __repr__(self) -> str:
        return (
            f"AuditEvent(sequence={self.sequence!r}, action={self.action!r}, "
            f"result={self.result.value})"
        )


# Events are chained in the order they were created, not the order SQLAlchemy
# happens to flush them.
_created = count()
_creation_order: "weakref.WeakKeyDictionary[AuditEvent, int]" = (
    weakref.WeakKeyDictionary()
)


def audit_hash(event: AuditEvent) -> str:
    """The SHA-256 hash of ``event``'s content and of the event before it.

    Computed over a canonical JSON rendering, so it depends only on the values,
    whichever database stored them.
    """
    content = {
        "sequence": event.sequence,
        "occurred_at": ensure_utc(event.occurred_at).isoformat(),
        "actor_type": ActorType(event.actor_type).value,
        "actor_id": event.actor_id,
        "action": event.action,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "input": event.input,
        "output": event.output,
        "result": AuditResult(event.result).value,
        "reason": event.reason,
        "evidence": event.evidence,
        "previous_hash": event.previous_hash,
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


# --- Protection of the workflow history and the audit log -----------------------


def _is_changed(record: object) -> bool:
    state = inspect(record, raiseerr=True)
    return any(
        state.attrs[column.key].history.has_changes()
        for column in state.mapper.column_attrs
    )


_HISTORY_TABLES = frozenset({"workflow_transition", "audit_event"})


def _describe(record: WorkflowTransition | AuditEvent) -> str:
    if isinstance(record, WorkflowTransition):
        return f"workflow transition {record.id}"
    return f"audit event {record.sequence}"


@event.listens_for(Session, "before_flush")
def _guard_workflow_history(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    for record in session.deleted:
        if isinstance(record, WorkflowTransition | AuditEvent):
            raise ImmutableHistoryError(
                f"{_describe(record)} cannot be deleted; the history is append-only"
            )
    for record in session.dirty:
        if isinstance(record, WorkflowTransition | AuditEvent) and _is_changed(record):
            raise ImmutableHistoryError(
                f"{_describe(record)} cannot be changed; the history is append-only"
            )


@event.listens_for(Session, "before_flush")
def _chain_audit_events(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    """Number and hash each new audit event after the last one recorded.

    Transactions append to the log one after another: each waits for any other
    that is appending to finish before it reads the last event, so two cannot
    both number theirs after the same one.
    """
    new = [record for record in session.new if isinstance(record, AuditEvent)]
    if not new:
        return
    new.sort(key=lambda record: _creation_order.get(record, -1))
    serialize(session.connection(), AUDIT_LOG_LOCK)
    last = session.execute(
        select(AuditEvent.sequence, AuditEvent.hash)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).first()
    sequence, previous = (0, None) if last is None else (last.sequence, last.hash)
    for record in new:
        if record.actor is not None:
            if record.actor.id is None:
                raise ValueError("an audit event's actor must be saved before it")
            record.actor_id = record.actor.id
        sequence += 1
        record.sequence = sequence
        record.previous_hash = previous
        record.hash = previous = audit_hash(record)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_history_changes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.name in _HISTORY_TABLES:
        raise ImmutableHistoryError(
            f"bulk writes to {table.name} are refused; the history is recorded by "
            "the workflow engine and is append-only"
        )

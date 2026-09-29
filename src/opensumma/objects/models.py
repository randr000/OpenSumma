"""Relational tables for Accounting Objects, their business events, and the links
to the journal entries that record their accounting impact.

The kernel knows nothing of these tables: no journal entry column points at an
object. The link lives here instead, so linking an object to a posted entry changes
nothing in the ledger, and the kernel remains usable on its own.

The end of this module guards the history these tables hold: objects are voided
rather than deleted, business events and links are never changed or removed, and a
voided object is final.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, event, inspect, select
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
    relationship,
)

from opensumma.db import Base, TimestampMixin, enum_check, enum_column
from opensumma.kernel.models import JournalEntry
from opensumma.objects.data import BusinessData
from opensumma.objects.enums import AccountingObjectStatus, AccountingObjectType
from opensumma.objects.errors import ImmutableRecordError, VoidedObjectError
from opensumma.utc import UtcDateTime, utcnow

SOURCE_LENGTH = 100
ENTITY_ID_LENGTH = 64
EVENT_TYPE_LENGTH = 64


class AccountingObject(TimestampMixin, Base):
    """A business document or event with accounting relevance, such as a vendor bill.

    ``occurred_at`` is when it happened in the business, ``source`` where it was
    observed, and ``entity_id`` the counterparty it concerns, such as a vendor or a
    customer. ``data`` holds its flexible business context as JSON.

    An object never affects the ledger itself: journal entries record its
    accounting impact, and the kernel validates and posts them like any other.
    """

    __tablename__ = "accounting_object"
    __table_args__ = (
        CheckConstraint("length(source) > 0", name="source_not_empty"),
        CheckConstraint(
            "entity_id IS NULL OR length(entity_id) > 0", name="entity_id_not_empty"
        ),
        enum_check("object_type", AccountingObjectType),
        enum_check("status", AccountingObjectStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[AccountingObjectType] = mapped_column(
        enum_column(AccountingObjectType), index=True
    )
    status: Mapped[AccountingObjectStatus] = mapped_column(
        enum_column(AccountingObjectStatus), default=AccountingObjectStatus.OBSERVED
    )
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    entity_id: Mapped[str | None] = mapped_column(String(ENTITY_ID_LENGTH), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(BusinessData, default=dict)

    events: Mapped[list["AccountingEvent"]] = relationship(
        back_populates="accounting_object",
        order_by=lambda: [AccountingEvent.occurred_at, AccountingEvent.id],
    )
    entry_links: Mapped[list["AccountingObjectEntry"]] = relationship(
        back_populates="accounting_object",
        order_by=lambda: AccountingObjectEntry.journal_entry_id,
    )

    def __init__(self, **kwargs: Any) -> None:
        # Column defaults apply only at flush; the status is needed before that.
        kwargs.setdefault("status", AccountingObjectStatus.OBSERVED)
        kwargs.setdefault("data", {})
        super().__init__(**kwargs)

    @property
    def is_voided(self) -> bool:
        return self.status is AccountingObjectStatus.VOIDED

    def __repr__(self) -> str:
        return (
            f"AccountingObject(id={self.id!r}, object_type={self.object_type.value!r}, "
            f"status={self.status.value})"
        )


class AccountingEvent(Base):
    """Something that happened to an accounting object in the business.

    A vendor bill is ``received`` or ``disputed``; goods on a purchase order are
    ``goods_received``; a contract is ``signed``. ``occurred_at`` is business time;
    ``created_at`` is when the event was recorded here.

    Events are facts, so they are never changed or deleted: a mistaken event is
    superseded by a later one.
    """

    __tablename__ = "accounting_event"
    __table_args__ = (
        CheckConstraint("length(event_type) > 0", name="event_type_not_empty"),
        CheckConstraint("length(source) > 0", name="source_not_empty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    accounting_object_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_object.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(EVENT_TYPE_LENGTH))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    data: Mapped[dict[str, Any]] = mapped_column(BusinessData, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    accounting_object: Mapped[AccountingObject] = relationship(back_populates="events")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("data", {})
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"AccountingEvent(id={self.id!r}, event_type={self.event_type!r})"


class AccountingObjectEntry(Base):
    """A journal entry that records part of an accounting object's impact.

    An object may be recorded by several entries (a bill and its later correction)
    and an entry may record several objects (one payment settling two bills). The
    link is permanent history; a wrong entry is reversed or voided, not unlinked.
    """

    __tablename__ = "accounting_object_entry"

    accounting_object_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_object.id"), primary_key=True
    )
    journal_entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entry.id"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    accounting_object: Mapped[AccountingObject] = relationship(
        back_populates="entry_links"
    )
    journal_entry: Mapped[JournalEntry] = relationship()

    def __repr__(self) -> str:
        return (
            "AccountingObjectEntry("
            f"accounting_object_id={self.accounting_object_id!r}, "
            f"journal_entry_id={self.journal_entry_id!r})"
        )


# --- Protection of object history ----------------------------------------------
#
# These hooks apply to every session, like the kernel's journal guards. Bulk
# INSERT, UPDATE, and DELETE statements on these tables are refused outright,
# because they would bypass the flush hook.

_OBJECT_TABLES = frozenset(
    {"accounting_object", "accounting_event", "accounting_object_entry"}
)
_RECORD_TYPES = (AccountingObject, AccountingEvent, AccountingObjectEntry)
_Record = AccountingObject | AccountingEvent | AccountingObjectEntry


def _identity(record: object) -> int | None:
    """The first primary-key value ``record`` was loaded with, or None if it is new."""
    key = inspect(record, raiseerr=True).identity
    return None if key is None else int(key[0])


def _changed_columns(record: object) -> set[str]:
    state = inspect(record, raiseerr=True)
    return {
        column.key
        for column in state.mapper.column_attrs
        if state.attrs[column.key].history.has_changes()
    }


def _linked_object_id(link: AccountingObjectEntry) -> int | None:
    if link.accounting_object is not None:
        return _identity(link.accounting_object)
    return link.accounting_object_id


def _recorded_voided(session: Session, object_ids: Iterable[int | None]) -> set[int]:
    """Which of these objects the database records as VOIDED.

    Read from the database rather than from attribute history, which SQLAlchemy
    forgets once an object has been expired, for instance by a commit.
    """
    ids = sorted({object_id for object_id in object_ids if object_id is not None})
    if not ids:
        return set()
    table = AccountingObject.__table__
    return set(
        session.scalars(
            select(table.c.id).where(
                table.c.id.in_(ids),
                table.c.status == AccountingObjectStatus.VOIDED.value,
            )
        )
    )


def _describe(record: _Record) -> str:
    if isinstance(record, AccountingObject):
        return f"accounting object {record.id}"
    if isinstance(record, AccountingEvent):
        return f"business event {record.id}"
    return (
        f"the link between accounting object {record.accounting_object_id} and "
        f"journal entry {record.journal_entry_id}"
    )


def _remedy(record: _Record) -> str:
    if isinstance(record, AccountingObject):
        return "void it instead"
    if isinstance(record, AccountingEvent):
        return "events are facts, so record a later event that supersedes it"
    return "reverse or void the journal entry instead"


@event.listens_for(Session, "before_flush")
def _guard_object_history(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    for record in session.deleted:
        if isinstance(record, _RECORD_TYPES):
            raise ImmutableRecordError(
                f"{_describe(record)} cannot be deleted; {_remedy(record)}"
            )

    changed_objects: list[AccountingObject] = []
    for record in session.dirty:
        if not isinstance(record, _RECORD_TYPES) or not _changed_columns(record):
            continue
        if isinstance(record, AccountingObject):
            changed_objects.append(record)
        else:
            raise ImmutableRecordError(
                f"{_describe(record)} cannot be changed; {_remedy(record)}"
            )

    new_links = [
        record for record in session.new if isinstance(record, AccountingObjectEntry)
    ]
    if not changed_objects and not new_links:
        return

    voided = _recorded_voided(
        session,
        [
            *(_identity(obj) for obj in changed_objects),
            *(_linked_object_id(link) for link in new_links),
        ],
    )
    for obj in changed_objects:
        if _identity(obj) in voided:
            raise VoidedObjectError(
                f"accounting object {obj.id} is VOIDED and cannot change"
            )
    for link in new_links:
        object_id = _linked_object_id(link)
        target = link.accounting_object
        if object_id in voided or (target is not None and target.is_voided):
            raise VoidedObjectError(
                f"accounting object {object_id} is VOIDED; "
                "no journal entry can record it"
            )


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_object_changes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.name in _OBJECT_TABLES:
        raise ImmutableRecordError(
            f"bulk writes to {table.name} are refused; write accounting objects "
            "through the session so that their history stays protected"
        )

"""Accounting Object services: record objects and their business events, link the
journal entries that record their accounting impact, and void objects.

An object never touches the ledger itself. The only way from an object to the
ledger is the flow the kernel already enforces:

    Accounting Object → accounting decision → Journal Entry → validation
        → posting → ledger

``create_journal_entry_for_object`` records the decision as a draft journal entry
through the kernel, so the kernel's recording rules apply; posting it is the
kernel's ``post_journal_entry``, so its posting rules apply too.

An object's accounting impact is derived, never stored: it is whatever the journal
entries that record it, and every reversal of them, have posted.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from opensumma.kernel import (
    Activity,
    EntryStatusError,
    JournalEntry,
    JournalEntryStatus,
    LineInput,
    create_journal_entry,
    posted_activity,
)
from opensumma.objects.counterparties import resolve_counterparty
from opensumma.objects.data import ensure_business_data
from opensumma.objects.enums import AccountingObjectStatus, AccountingObjectType
from opensumma.objects.errors import (
    AlreadyLinkedError,
    ObjectHasAccountingImpactError,
    UnknownAccountingObjectError,
    VoidedObjectError,
)
from opensumma.objects.models import (
    EVENT_TYPE_LENGTH,
    SOURCE_LENGTH,
    AccountingEvent,
    AccountingObject,
    AccountingObjectEntry,
    Counterparty,
)
from opensumma.utc import ensure_utc

_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class ImpactEntry:
    """A journal entry that records an accounting object, or reverses one that does.

    ``linked`` is False for an entry reached only as a reversal of a linked one.
    """

    entry_id: int
    entry_date: date
    description: str
    status: JournalEntryStatus
    reversal_of: int | None
    linked: bool


@dataclass(frozen=True)
class AccountingImpact:
    """What the ledger holds for an accounting object.

    ``entries`` are the journal entries linked to the object together with every
    reversal of them, by id. ``activity`` is what those entries have posted, per
    account code; drafts and voided entries contribute nothing.
    """

    object_id: int
    entries: tuple[ImpactEntry, ...]
    activity: Mapping[str, Activity]

    @property
    def pending_entry_ids(self) -> tuple[int, ...]:
        """Entries that are not yet final, and so could still be posted."""
        return tuple(
            entry.entry_id for entry in self.entries if not entry.status.is_final
        )

    @property
    def posted_entry_ids(self) -> tuple[int, ...]:
        """Entries in the ledger that have not been reversed."""
        return tuple(
            entry.entry_id
            for entry in self.entries
            if entry.status is JournalEntryStatus.POSTED
        )

    @property
    def has_net_impact(self) -> bool:
        """True while the ledger still carries a balance for the object."""
        return any(
            activity.debits != activity.credits for activity in self.activity.values()
        )


def create_accounting_object(
    session: Session,
    *,
    object_type: AccountingObjectType | str,
    occurred_at: datetime,
    source: str,
    counterparty: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> AccountingObject:
    """Record a business document or event as an OBSERVED accounting object.

    ``occurred_at`` is when it happened in the business, a timezone-aware
    timestamp; it is never defaulted, so what is recorded does not depend on when
    code runs. ``counterparty`` is the code of the vendor or customer it concerns,
    if known; it must be active and the kind the object's type calls for. ``data``
    is JSON business context, with amounts written as strings.
    """
    kind = AccountingObjectType(object_type)
    obj = AccountingObject(
        object_type=kind,
        occurred_at=ensure_utc(occurred_at),
        source=_text(source, "source", SOURCE_LENGTH),
        counterparty=None
        if counterparty is None
        else resolve_counterparty(session, counterparty, object_type=kind),
        data=ensure_business_data({} if data is None else data),
    )
    session.add(obj)
    return obj


def assign_counterparty(
    session: Session, obj: AccountingObject, counterparty: str
) -> None:
    """Settle which vendor or customer ``obj`` concerns.

    The counterparty must be active and the kind the object's type calls for.
    """
    if obj.is_voided:
        raise VoidedObjectError(f"{_label(obj)} is VOIDED and cannot change")
    obj.counterparty = resolve_counterparty(
        session, counterparty, object_type=obj.object_type
    )


def get_accounting_object(session: Session, object_id: int) -> AccountingObject:
    """Return the accounting object with ``object_id``, or raise."""
    obj = session.get(AccountingObject, object_id)
    if obj is None:
        raise UnknownAccountingObjectError(f"no accounting object with id {object_id}")
    return obj


def search_accounting_objects(
    session: Session,
    *,
    object_type: AccountingObjectType | str | None = None,
    status: AccountingObjectStatus | str | None = None,
    counterparty: str | None = None,
    source: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    data: Mapping[str, str] | None = None,
) -> list[AccountingObject]:
    """Accounting objects matching every filter given, by occurrence, then id.

    ``occurred_from`` and ``occurred_to`` are inclusive. ``data`` matches top-level
    business-data fields that hold strings, such as an invoice number, exactly.
    """
    where: list[ColumnElement[bool]] = []
    if object_type is not None:
        where.append(AccountingObject.object_type == AccountingObjectType(object_type))
    if status is not None:
        where.append(AccountingObject.status == AccountingObjectStatus(status))
    if counterparty is not None:
        named = AccountingObject.counterparty.has(Counterparty.code == counterparty)
        where.append(named)
    if source is not None:
        where.append(AccountingObject.source == source)
    start = None if occurred_from is None else ensure_utc(occurred_from)
    end = None if occurred_to is None else ensure_utc(occurred_to)
    if start is not None and end is not None and start > end:
        raise ValueError(f"a time range cannot start ({start}) after it ends ({end})")
    if start is not None:
        where.append(AccountingObject.occurred_at >= start)
    if end is not None:
        where.append(AccountingObject.occurred_at <= end)
    for key, value in sorted((data or {}).items()):
        if not isinstance(value, str):
            raise TypeError(
                f"data filters match string fields; {key} is {type(value).__name__}"
            )
        where.append(AccountingObject.data[key].as_string() == value)

    statement = (
        select(AccountingObject)
        .where(*where)
        .order_by(AccountingObject.occurred_at, AccountingObject.id)
    )
    return list(session.scalars(statement))


def record_accounting_event(
    session: Session,
    obj: AccountingObject,
    *,
    event_type: str,
    occurred_at: datetime,
    source: str,
    data: Mapping[str, Any] | None = None,
) -> AccountingEvent:
    """Record something that happened to ``obj`` in the business.

    ``event_type`` is lowercase snake_case, such as ``goods_received``. Events are
    facts, so a voided object may still receive them, and none is ever changed.
    """
    kind = _text(event_type, "event_type", EVENT_TYPE_LENGTH)
    if not _EVENT_TYPE.fullmatch(kind):
        raise ValueError(
            f"event types are lowercase snake_case, such as 'goods_received'; "
            f"got {kind!r}"
        )
    recorded = AccountingEvent(
        accounting_object=obj,
        event_type=kind,
        occurred_at=ensure_utc(occurred_at),
        source=_text(source, "source", SOURCE_LENGTH),
        data=ensure_business_data({} if data is None else data),
    )
    session.add(recorded)
    return recorded


def link_journal_entry(
    session: Session, obj: AccountingObject, entry: JournalEntry
) -> AccountingObjectEntry:
    """Record that ``entry`` records part of ``obj``'s accounting impact.

    Linking changes nothing in the ledger and nothing about the entry, so an entry
    that is already posted can be linked after the fact.
    """
    if obj.is_voided:
        raise VoidedObjectError(
            f"{_label(obj)} is VOIDED; no journal entry can record it"
        )
    if entry.status is JournalEntryStatus.VOIDED:
        raise EntryStatusError(
            f"journal entry {entry.id} is VOIDED and records nothing", entry.status
        )
    if any(link.journal_entry is entry for link in obj.entry_links):
        raise AlreadyLinkedError(
            f"journal entry {entry.id} already records {_label(obj)}"
        )
    link = AccountingObjectEntry(accounting_object=obj, journal_entry=entry)
    session.add(link)
    return link


def create_journal_entry_for_object(
    session: Session,
    obj: AccountingObject,
    *,
    entry_date: date,
    description: str,
    lines: Sequence[LineInput],
) -> JournalEntry:
    """Record an accounting decision about ``obj`` as a DRAFT journal entry.

    The kernel creates the entry, so its recording rules apply: a
    ``JournalEntryError`` lists everything wrong, and nothing is recorded or linked.
    The entry reaches the ledger only through the kernel's ``post_journal_entry``.
    """
    if obj.is_voided:
        raise VoidedObjectError(
            f"{_label(obj)} is VOIDED; no journal entry can record it"
        )
    entry = create_journal_entry(
        session, entry_date=entry_date, description=description, lines=lines
    )
    link_journal_entry(session, obj, entry)
    return entry


def objects_for_journal_entry(
    session: Session, entry: JournalEntry
) -> list[AccountingObject]:
    """The accounting objects ``entry`` records, by id."""
    session.flush()
    statement = (
        select(AccountingObject)
        .join(AccountingObject.entry_links)
        .where(AccountingObjectEntry.journal_entry_id == entry.id)
        .order_by(AccountingObject.id)
    )
    return list(session.scalars(statement))


def accounting_impact(session: Session, obj: AccountingObject) -> AccountingImpact:
    """What the ledger holds for ``obj``, read through the kernel's ledger.

    Reversals of a linked entry count as part of the object's impact whether or not
    they were linked themselves, so an entry and its reversal net to nothing here as
    they do in the ledger.
    """
    session.flush()  # pending entries and links need ids before the ledger is read
    linked = set(
        session.scalars(
            select(AccountingObjectEntry.journal_entry_id).where(
                AccountingObjectEntry.accounting_object_id == obj.id
            )
        )
    )
    entry_ids = set(linked)
    frontier = linked
    while frontier:
        reversals = select(JournalEntry.id).where(
            JournalEntry.reversal_of_id.in_(sorted(frontier))
        )
        frontier = set(session.scalars(reversals)) - entry_ids
        entry_ids |= frontier

    entries = session.scalars(
        select(JournalEntry)
        .where(JournalEntry.id.in_(sorted(entry_ids)))
        .order_by(JournalEntry.id)
    )
    return AccountingImpact(
        object_id=obj.id,
        entries=tuple(
            ImpactEntry(
                entry_id=entry.id,
                entry_date=entry.entry_date,
                description=entry.description,
                status=entry.status,
                reversal_of=entry.reversal_of_id,
                linked=entry.id in linked,
            )
            for entry in entries
        ),
        activity=posted_activity(session, entry_ids=entry_ids),
    )


def void_accounting_object(session: Session, obj: AccountingObject) -> None:
    """Withdraw ``obj``, such as a duplicate bill, without deleting it.

    The ledger must no longer carry it. Journal entries that could still be posted
    are voided first and posted ones reversed through the kernel, so that its
    impact nets to nothing on every account. Voiding is final.
    """
    if obj.is_voided:
        raise VoidedObjectError(f"{_label(obj)} is already VOIDED")
    impact = accounting_impact(session, obj)
    if impact.pending_entry_ids:
        raise ObjectHasAccountingImpactError(
            f"{_label(obj)} has journal entries that could still be posted "
            f"({_ids(impact.pending_entry_ids)}); void them first",
            impact.pending_entry_ids,
        )
    if impact.has_net_impact:
        raise ObjectHasAccountingImpactError(
            f"{_label(obj)} is still recorded in the ledger by journal entries "
            f"{_ids(impact.posted_entry_ids)}; reverse them first",
            impact.posted_entry_ids,
        )
    obj.status = AccountingObjectStatus.VOIDED


def _text(value: str, field: str, max_length: int) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > max_length:
        raise ValueError(f"{field} is limited to {max_length} characters")
    return text


def _label(obj: AccountingObject) -> str:
    return "the accounting object" if obj.id is None else f"accounting object {obj.id}"


def _ids(entry_ids: Sequence[int]) -> str:
    return ", ".join(str(entry_id) for entry_id in entry_ids)

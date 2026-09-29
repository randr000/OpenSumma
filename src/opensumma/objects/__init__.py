"""Accounting Objects: business documents and events, and their accounting impact.

An Accounting Object is a vendor bill, a customer invoice, a payment, a bank
transaction, or another business event with accounting relevance. It carries its
business context as JSON, a history of business events, and links to the journal
entries that record its accounting impact.

This layer sits above the accounting kernel and depends on it; the kernel never
depends on this layer, so no report can read an object. Objects reach the ledger
only through journal entries that the kernel validates and posts.

Importing this package registers its persistence models on ``Base.metadata`` and
the session hooks that protect object history.
"""

from opensumma.objects.data import BusinessData, ensure_business_data
from opensumma.objects.enums import AccountingObjectStatus, AccountingObjectType
from opensumma.objects.errors import (
    AccountingObjectError,
    AlreadyLinkedError,
    ImmutableRecordError,
    ObjectHasAccountingImpactError,
    UnknownAccountingObjectError,
    VoidedObjectError,
)
from opensumma.objects.models import (
    AccountingEvent,
    AccountingObject,
    AccountingObjectEntry,
)
from opensumma.objects.services import (
    AccountingImpact,
    ImpactEntry,
    accounting_impact,
    create_accounting_object,
    create_journal_entry_for_object,
    get_accounting_object,
    link_journal_entry,
    objects_for_journal_entry,
    record_accounting_event,
    search_accounting_objects,
    void_accounting_object,
)

__all__ = [
    "AccountingEvent",
    "AccountingImpact",
    "AccountingObject",
    "AccountingObjectEntry",
    "AccountingObjectError",
    "AccountingObjectStatus",
    "AccountingObjectType",
    "AlreadyLinkedError",
    "BusinessData",
    "ImmutableRecordError",
    "ImpactEntry",
    "ObjectHasAccountingImpactError",
    "UnknownAccountingObjectError",
    "VoidedObjectError",
    "accounting_impact",
    "create_accounting_object",
    "create_journal_entry_for_object",
    "ensure_business_data",
    "get_accounting_object",
    "link_journal_entry",
    "objects_for_journal_entry",
    "record_accounting_event",
    "search_accounting_objects",
    "void_accounting_object",
]

"""Accounting Objects: business documents and events, and their accounting impact.

An Accounting Object is a vendor bill, a customer invoice, a payment, a bank
transaction, or another business event with accounting relevance. It names the
counterparty it concerns, carries its business context as JSON and a history of
business events, and is linked to the journal entries that record its accounting
impact. Counterparties, the vendors and customers, are this layer's master data.

This layer sits above the accounting kernel and depends on it; the kernel never
depends on this layer, so no report can read an object. Objects reach the ledger
only through journal entries that the kernel validates and posts.

Importing this package registers its persistence models on ``Base.metadata`` and
the session hooks that protect object history.
"""

from opensumma.objects.counterparties import (
    DEFAULT_COUNTERPARTIES,
    CounterpartySpec,
    activate_counterparty,
    counterparties,
    create_counterparty,
    deactivate_counterparty,
    find_counterparty,
    get_counterparty,
    resolve_counterparty,
    seed_counterparties,
)
from opensumma.objects.data import BusinessData, ensure_business_data
from opensumma.objects.enums import (
    AccountingObjectStatus,
    AccountingObjectType,
    CounterpartyKind,
)
from opensumma.objects.errors import (
    AccountingObjectError,
    AlreadyLinkedError,
    CounterpartyKindError,
    ImmutableRecordError,
    InactiveCounterpartyError,
    ObjectHasAccountingImpactError,
    UnknownAccountingObjectError,
    UnknownCounterpartyError,
    VoidedObjectError,
)
from opensumma.objects.models import (
    AccountingEvent,
    AccountingObject,
    AccountingObjectEntry,
    Counterparty,
)
from opensumma.objects.services import (
    AccountingImpact,
    ImpactEntry,
    accounting_impact,
    assign_counterparty,
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
    "DEFAULT_COUNTERPARTIES",
    "AccountingEvent",
    "AccountingImpact",
    "AccountingObject",
    "AccountingObjectEntry",
    "AccountingObjectError",
    "AccountingObjectStatus",
    "AccountingObjectType",
    "AlreadyLinkedError",
    "BusinessData",
    "Counterparty",
    "CounterpartyKind",
    "CounterpartyKindError",
    "CounterpartySpec",
    "ImmutableRecordError",
    "ImpactEntry",
    "InactiveCounterpartyError",
    "ObjectHasAccountingImpactError",
    "UnknownAccountingObjectError",
    "UnknownCounterpartyError",
    "VoidedObjectError",
    "accounting_impact",
    "activate_counterparty",
    "assign_counterparty",
    "counterparties",
    "create_accounting_object",
    "create_counterparty",
    "create_journal_entry_for_object",
    "deactivate_counterparty",
    "ensure_business_data",
    "find_counterparty",
    "get_accounting_object",
    "get_counterparty",
    "link_journal_entry",
    "objects_for_journal_entry",
    "record_accounting_event",
    "resolve_counterparty",
    "search_accounting_objects",
    "seed_counterparties",
    "void_accounting_object",
]

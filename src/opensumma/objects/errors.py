"""Rules the Accounting Object layer enforces.

Journal entries created for an object are still subject to the kernel's rules and
fail with the kernel's errors; these are the object layer's own.
"""

from collections.abc import Sequence


class AccountingObjectError(Exception):
    """Base class for every rule the Accounting Object layer enforces."""


class UnknownAccountingObjectError(AccountingObjectError):
    """No accounting object exists with the given id."""


class VoidedObjectError(AccountingObjectError):
    """The accounting object is voided: it cannot change or gain journal entries."""


class AlreadyLinkedError(AccountingObjectError):
    """The journal entry already records the accounting object."""


class ObjectHasAccountingImpactError(AccountingObjectError):
    """The object cannot be voided while journal entries still record it.

    ``entry_ids`` names the entries to deal with first: drafts to void, or posted
    entries to reverse.
    """

    def __init__(self, message: str, entry_ids: Sequence[int]) -> None:
        self.entry_ids = tuple(entry_ids)
        super().__init__(message)


class ImmutableRecordError(AccountingObjectError):
    """An accounting object, business event, or link was deleted or rewritten.

    Objects are voided rather than deleted, business events are facts that a later
    event supersedes, and the link between an object and a journal entry is
    permanent history.
    """

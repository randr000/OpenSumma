"""Accounting rule violations.

Every rule the kernel enforces fails with a specific exception rather than a
generic one. Callers, and eventually agents, need to know *which* rule they
broke in order to correct a proposal.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from opensumma.kernel.enums import IssueCode, JournalEntryStatus


class KernelError(Exception):
    """Base class for every accounting rule the kernel enforces."""


@dataclass(frozen=True)
class ValidationIssue:
    """One reason a journal entry cannot be recorded or posted."""

    code: IssueCode
    message: str
    line_number: int | None = None

    def __str__(self) -> str:
        if self.line_number is None:
            return self.message
        return f"line {self.line_number}: {self.message}"


class JournalEntryError(KernelError):
    """A journal entry breaks accounting rules; ``issues`` lists every one of them.

    All issues are reported together rather than one at a time, so a proposal can
    be corrected in a single pass.
    """

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(str(issue) for issue in self.issues))


class UnknownJournalEntryError(KernelError):
    """No journal entry exists with the given id."""


class EntryStatusError(KernelError):
    """The journal entry's status does not allow the requested operation."""

    def __init__(self, message: str, status: JournalEntryStatus) -> None:
        self.status = status
        super().__init__(message)


class AlreadyPostedError(EntryStatusError):
    """The journal entry is already in the ledger, so it cannot be posted again."""


class ImmutableEntryError(KernelError):
    """A posted, reversed, or voided journal entry was changed.

    Such entries are never edited or deleted. A posted entry is corrected by
    reversing it and posting a new one.
    """


class AccountHasPostingsError(KernelError):
    """The account already has posted lines, so it cannot become a parent.

    Giving it a child would turn an account that holds ledger history into an
    aggregate that is not allowed to hold any.
    """


class DuplicateCodeError(KernelError):
    """The code identifying an account, period, or dimension is already taken."""


class UnknownAccountError(KernelError):
    """No account exists with the given code."""


class AccountTypeMismatchError(KernelError):
    """An account's type differs from its parent's, so it cannot roll up into it."""


class InactiveAccountError(KernelError):
    """An inactive account cannot receive postings or be activated under an
    inactive parent."""


class NotPostableError(KernelError):
    """The account only aggregates its children, so nothing may be posted to it."""


class UnknownPeriodError(KernelError):
    """No accounting period exists with the given code, or contains the given date."""


class InvalidPeriodRangeError(KernelError):
    """An accounting period ends before it starts."""


class OverlappingPeriodError(KernelError):
    """An accounting period overlaps an existing one, so a date would have two."""


class ClosedPeriodError(KernelError):
    """A closed accounting period accepts no postings."""


class UnknownDimensionError(KernelError):
    """No dimension exists with the given code."""


class UnknownDimensionValueError(KernelError):
    """The dimension has no value with the given code."""


class InactiveDimensionValueError(KernelError):
    """The dimension value exists but is no longer available for use."""

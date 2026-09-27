"""Accounting rule violations.

Every rule the kernel enforces fails with a specific exception rather than a
generic one. Callers, and eventually agents, need to know *which* rule they
broke in order to correct a proposal.
"""


class KernelError(Exception):
    """Base class for every accounting rule the kernel enforces."""


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

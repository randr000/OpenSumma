"""Rules the workflow engine enforces.

Content the kernel or the object layer rejects still fails with their errors, such
as ``JournalEntryError``; these are the workflow's own: who may act, and when.
"""

from collections.abc import Sequence
from enum import StrEnum


class WorkflowError(Exception):
    """Base class for every rule the workflow engine enforces."""


class UnknownActorError(WorkflowError):
    """No actor exists with the given code."""


class PermissionDeniedError(WorkflowError):
    """The actor lacks the permission the action needs, or is inactive."""

    def __init__(self, message: str, permission: StrEnum) -> None:
        self.permission = permission
        super().__init__(message)


class InvalidTransitionError(WorkflowError):
    """The action is not allowed from the subject's current state."""

    def __init__(self, message: str, action: StrEnum, status: StrEnum | None) -> None:
        self.action = action
        self.status = status
        super().__init__(message)


class SegregationOfDutiesError(WorkflowError):
    """An actor tried to approve a journal entry they prepared."""


class CounterpartyRequiredError(WorkflowError):
    """The object's type names a vendor or customer, and none has been settled."""


class PeriodSequenceError(WorkflowError):
    """Periods close in order and reopen in reverse order.

    ``period_codes`` names the periods that must be closed, or reopened, first.
    """

    def __init__(self, message: str, period_codes: Sequence[str]) -> None:
        self.period_codes = tuple(period_codes)
        super().__init__(message)


class PendingEntriesError(WorkflowError):
    """A period cannot close while entries dated in it could still be posted.

    ``entry_ids`` names them: each must be posted or voided first.
    """

    def __init__(self, message: str, entry_ids: Sequence[int]) -> None:
        self.entry_ids = tuple(entry_ids)
        super().__init__(message)


class ImmutableHistoryError(WorkflowError):
    """A workflow transition was changed or deleted; the history is append-only."""

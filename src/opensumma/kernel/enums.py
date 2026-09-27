"""The kernel's closed vocabularies.

These are stored as strings so a database row is readable on its own, and are
constrained at the database level as well as in Python.
"""

from enum import StrEnum


class NormalBalance(StrEnum):
    """The side on which an account normally carries its balance."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class AccountType(StrEnum):
    """The five fundamental account classifications."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"

    @property
    def normal_balance(self) -> NormalBalance:
        """The normal balance of an ordinary account of this type.

        Contra accounts carry the opposite side and say so explicitly; see
        ``Account.normal_balance``.
        """
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return NormalBalance.DEBIT
        return NormalBalance.CREDIT


class PeriodStatus(StrEnum):
    """Whether an accounting period still accepts postings."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class JournalEntryStatus(StrEnum):
    """Where a journal entry is in its life.

    The kernel moves entries between DRAFT, POSTED, REVERSED, and VOIDED. PROPOSED,
    PENDING_APPROVAL, and APPROVED belong to the workflow engine (Phase 5); the
    kernel treats them like DRAFT, as not yet recorded.
    """

    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    REVERSED = "REVERSED"
    VOIDED = "VOIDED"

    @property
    def in_ledger(self) -> bool:
        """True once the entry is part of the ledger.

        A reversed entry stays in the ledger: it and its reversal both remain, and
        together they net to nothing.
        """
        return self in (JournalEntryStatus.POSTED, JournalEntryStatus.REVERSED)

    @property
    def is_final(self) -> bool:
        """True when the entry can no longer change, apart from being reversed."""
        return self.in_ledger or self is JournalEntryStatus.VOIDED


class IssueCode(StrEnum):
    """Why a journal entry cannot be recorded or posted.

    Codes are stable identifiers, so callers and benchmarks can compare them exactly
    instead of parsing messages.
    """

    # The entry cannot be stored at all.
    MISSING_DESCRIPTION = "MISSING_DESCRIPTION"
    TEXT_TOO_LONG = "TEXT_TOO_LONG"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    DEBIT_AND_CREDIT = "DEBIT_AND_CREDIT"
    ZERO_AMOUNT = "ZERO_AMOUNT"
    UNKNOWN_ACCOUNT = "UNKNOWN_ACCOUNT"
    UNKNOWN_DIMENSION = "UNKNOWN_DIMENSION"
    UNKNOWN_DIMENSION_VALUE = "UNKNOWN_DIMENSION_VALUE"

    # The entry can be stored as a draft but not posted.
    TOO_FEW_LINES = "TOO_FEW_LINES"
    UNBALANCED = "UNBALANCED"
    ACCOUNT_NOT_POSTABLE = "ACCOUNT_NOT_POSTABLE"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    NO_PERIOD = "NO_PERIOD"
    PERIOD_CLOSED = "PERIOD_CLOSED"
    DIMENSION_VALUE_INACTIVE = "DIMENSION_VALUE_INACTIVE"

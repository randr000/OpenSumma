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

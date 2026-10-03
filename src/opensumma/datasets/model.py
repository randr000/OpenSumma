"""A dataset's plan: the business transactions of a year, before any is recorded.

The planner and the error injector work on these values alone, with no database,
so a plan can be built, checked, and changed quickly; the recorder then writes it
into the books through the kernel and the object layer.

A ``Transaction`` becomes exactly one journal entry. Its ``document`` becomes the
accounting object that entry records, and its ``bank_line`` a separate
``bank_transaction`` object: the operating account's statement line for the money
it moved. A transaction whose document is itself the statement line, such as a bank
fee, has no separate ``bank_line``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from opensumma.money import ZERO
from opensumma.objects import AccountingObjectStatus, AccountingObjectType

# The operating bank account, whose statement is the dataset's bank feed.
OPERATING_ACCOUNT = "1111"


@dataclass(frozen=True)
class Line:
    """One journal line: its signed ``amount``, debits positive and credits
    negative. ``dimensions`` holds ``(dimension, value)`` pairs, sorted."""

    account: str
    amount: Decimal
    dimensions: tuple[tuple[str, str], ...] = ()

    def dimension(self, code: str) -> str | None:
        return dict(self.dimensions).get(code)


def dims(**values: str) -> tuple[tuple[str, str], ...]:
    """Dimension pairs from keywords, such as ``dims(DEPARTMENT="ENG")``."""
    return tuple(sorted(values.items()))


@dataclass(frozen=True)
class Document:
    """The business document a transaction records, as an accounting object.

    ``data`` is JSON business context whose amounts are strings. Treat it as
    immutable: build a new document to change it.
    """

    object_type: AccountingObjectType
    occurred_at: datetime
    source: str
    data: Mapping[str, Any]
    counterparty: str | None = None
    status: AccountingObjectStatus = AccountingObjectStatus.CLASSIFIED


@dataclass(frozen=True)
class BankLine:
    """A line of the operating account's bank statement.

    ``amount`` is signed as the bank shows it: deposits positive, withdrawals
    negative.
    """

    posted_on: date
    amount: Decimal
    description: str
    reference: str


@dataclass(frozen=True)
class Transaction:
    """A business transaction, recorded as one journal entry.

    ``key`` identifies it within the plan; the books never show it. ``reverses``
    names the transaction this one reverses, which the kernel records as a
    reversal of that entry.
    """

    key: str
    kind: str
    entry_date: date
    description: str
    lines: tuple[Line, ...]
    document: Document | None = None
    bank_line: BankLine | None = None
    reverses: str | None = None

    @property
    def is_balanced(self) -> bool:
        return sum((line.amount for line in self.lines), ZERO) == ZERO

    @property
    def amount(self) -> Decimal:
        """The total of the entry's debits."""
        return sum((line.amount for line in self.lines if line.amount > ZERO), ZERO)


@dataclass(frozen=True)
class StatementLine:
    """A bank statement line that no transaction records."""

    key: str
    bank_line: BankLine


@dataclass(frozen=True)
class CounterpartyEntry:
    """A vendor or customer the books need beyond the default cast."""

    code: str
    name: str
    kind: str


@dataclass(frozen=True)
class Plan:
    """A year of a company's business, ready to record.

    ``transactions`` and ``statement_lines`` are recorded together in date order,
    so the books' ids follow the calendar as a real system's roughly do.
    """

    year: int
    counterparties: tuple[CounterpartyEntry, ...]
    transactions: tuple[Transaction, ...]
    statement_lines: tuple[StatementLine, ...] = ()

    def transaction(self, key: str) -> Transaction:
        for transaction in self.transactions:
            if transaction.key == key:
                return transaction
        raise KeyError(key)

    def in_order(self) -> list[Transaction | StatementLine]:
        """Everything to record, by date, then key."""
        items: list[Transaction | StatementLine] = [
            *self.transactions,
            *self.statement_lines,
        ]
        return sorted(items, key=_order)


def _order(item: Transaction | StatementLine) -> tuple[date, str]:
    if isinstance(item, Transaction):
        return item.entry_date, item.key
    return item.bank_line.posted_on, item.key


def net_by_account(
    transactions: Iterable[Transaction], *, through: date | None = None
) -> dict[str, Decimal]:
    """Each account's net activity (debits positive) in ``transactions`` dated up
    to ``through``, leaving out accounts that net to zero."""
    totals: dict[str, Decimal] = {}
    for transaction in transactions:
        if through is not None and transaction.entry_date > through:
            continue
        for line in transaction.lines:
            totals[line.account] = totals.get(line.account, ZERO) + line.amount
    return {account: net for account, net in sorted(totals.items()) if net != ZERO}

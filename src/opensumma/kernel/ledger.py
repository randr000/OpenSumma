"""The ledger: every line of every journal entry that has been posted.

The ledger is not a copy of the journal. It is the set of journal lines whose entry
is POSTED or REVERSED; a reversed entry stays in the ledger beside the reversal that
offsets it. Drafts, proposals, and voided entries are never part of it.

It is immutable and append-only because posted entries are: once an entry reaches
the ledger, its lines never change and it never leaves, which the session hooks in
``models`` enforce. Reports read the ledger through this module and nothing else,
so no report can see anything that has not been posted.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, contains_eager, selectinload

from opensumma.kernel.accounts import descendants, get_account
from opensumma.kernel.enums import LEDGER_STATUSES, AccountType, NormalBalance
from opensumma.kernel.models import (
    Account,
    DimensionValue,
    JournalEntry,
    JournalLine,
    JournalLineDimension,
)
from opensumma.money import ZERO
from opensumma.utc import ensure_date


@dataclass(frozen=True)
class Activity:
    """Posted debits and credits, on one account or summed over several."""

    debits: Decimal = ZERO
    credits: Decimal = ZERO

    def __add__(self, other: "Activity") -> "Activity":
        return Activity(self.debits + other.debits, self.credits + other.credits)

    def balance(self, normal_balance: NormalBalance) -> Decimal:
        """The balance stated on the ``normal_balance`` side.

        Positive when the activity leaves a balance on that side, negative when it
        leaves one on the other.
        """
        if normal_balance is NormalBalance.DEBIT:
            return self.debits - self.credits
        return self.credits - self.debits


@dataclass(frozen=True)
class AccountBalance:
    """An account's posted debits and credits, and the balance they leave.

    ``balance`` is stated in the account's normal direction, so it is positive
    when the account carries its usual balance: a bank account with money in it,
    a payable that is owed, accumulated depreciation that has built up.
    """

    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    as_of: date | None
    debits: Decimal
    credits: Decimal
    balance: Decimal


@dataclass(frozen=True)
class LedgerLine:
    """One line of the ledger, with the entry it belongs to.

    ``dimensions`` holds ``(dimension code, value code)`` pairs, by dimension code.
    """

    entry_id: int
    entry_date: date
    line_number: int
    account_code: str
    description: str
    memo: str | None
    debit: Decimal
    credit: Decimal
    dimensions: tuple[tuple[str, str], ...]


def posted_activity(
    session: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    account_codes: Iterable[str] | None = None,
) -> dict[str, Activity]:
    """Posted debits and credits per account code, for entries dated in range.

    Both bounds are inclusive and either may be omitted. Accounts with no posted
    lines in the range are absent from the result.
    """
    where = _ledger_filter(start, end)
    if account_codes is not None:
        where.append(Account.code.in_(sorted(set(account_codes))))
    return _activity(session, where)


def account_balance(
    session: Session, code: str, *, as_of: date | None = None
) -> AccountBalance:
    """The balance of account ``code`` from its posted lines dated up to ``as_of``.

    Without ``as_of``, the whole ledger counts. A parent account's balance is the
    sum of the accounts below it, stated in the parent's normal direction, so a
    contra account reduces it: fixed assets are shown net of depreciation.
    """
    account = get_account(session, code)
    codes = [account.code, *(below.code for below in descendants(account))]
    activity = sum(
        posted_activity(session, end=as_of, account_codes=codes).values(), Activity()
    )
    return AccountBalance(
        account_code=account.code,
        account_name=account.name,
        account_type=account.account_type,
        normal_balance=account.normal_balance,
        as_of=as_of,
        debits=activity.debits,
        credits=activity.credits,
        balance=activity.balance(account.normal_balance),
    )


def ledger_lines(
    session: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    account_codes: Iterable[str] | None = None,
) -> list[LedgerLine]:
    """Ledger lines dated in range, by date, then entry, then line number.

    Both bounds are inclusive and either may be omitted.
    """
    where = _ledger_filter(start, end)
    if account_codes is not None:
        where.append(Account.code.in_(sorted(set(account_codes))))
    statement = (
        select(JournalLine)
        .join(JournalLine.entry)
        .join(JournalLine.account)
        .where(*where)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.line_number)
        .options(
            contains_eager(JournalLine.entry),
            contains_eager(JournalLine.account),
            selectinload(JournalLine.dimensions)
            .joinedload(JournalLineDimension.value)
            .joinedload(DimensionValue.dimension),
        )
    )
    return [_ledger_line(line) for line in session.scalars(statement)]


def activity_before(
    session: Session, before: date, *, account_codes: Iterable[str] | None = None
) -> dict[str, Activity]:
    """Posted debits and credits per account code, for entries dated before ``before``.

    This is what an account brings into a period: its opening balance.
    """
    where: list[ColumnElement[bool]] = [
        JournalEntry.status.in_(LEDGER_STATUSES),
        JournalEntry.entry_date < ensure_date(before),
    ]
    if account_codes is not None:
        where.append(Account.code.in_(sorted(set(account_codes))))
    return _activity(session, where)


def _activity(
    session: Session, where: list[ColumnElement[bool]]
) -> dict[str, Activity]:
    # Money columns hold integer cents, so SUM is exact in SQL and comes back as a
    # two-decimal Decimal.
    statement = (
        select(Account.code, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .select_from(JournalLine)
        .join(JournalLine.entry)
        .join(JournalLine.account)
        .where(*where)
        .group_by(Account.code)
    )
    return {
        code: Activity(debits, credits)
        for code, debits, credits in session.execute(statement)
    }


def _ledger_filter(start: date | None, end: date | None) -> list[ColumnElement[bool]]:
    if start is not None:
        ensure_date(start)
    if end is not None:
        ensure_date(end)
    if start is not None and end is not None and start > end:
        raise ValueError(f"a date range cannot start ({start}) after it ends ({end})")
    where: list[ColumnElement[bool]] = [JournalEntry.status.in_(LEDGER_STATUSES)]
    if start is not None:
        where.append(JournalEntry.entry_date >= start)
    if end is not None:
        where.append(JournalEntry.entry_date <= end)
    return where


def _ledger_line(line: JournalLine) -> LedgerLine:
    return LedgerLine(
        entry_id=line.entry.id,
        entry_date=line.entry.entry_date,
        line_number=line.line_number,
        account_code=line.account.code,
        description=line.entry.description,
        memo=line.memo,
        debit=line.debit,
        credit=line.credit,
        dimensions=tuple(
            sorted(
                (tag.value.dimension.code, tag.value.code) for tag in line.dimensions
            )
        ),
    )

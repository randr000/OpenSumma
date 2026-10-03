"""Financial reports: trial balance, general ledger, income statement, balance sheet.

Every figure comes from ``opensumma.kernel.ledger``, so a report reflects exactly
what has been posted: never a draft, a proposal, or a voided entry, and never an
Accounting Object. Reports are plain frozen values, detached from the database.

Reports include every account the ledger touches, active or not: an account retired
after it was posted to still holds that history, and leaving it out would unbalance
the report.

Sign conventions:

- The trial balance and the general ledger are signed as the ledger is: a debit,
  or a debit balance, is positive, and a credit, or a credit balance, negative. The
  trial balance's balances therefore sum to zero.
- A financial statement states each amount in its section's normal direction, so
  revenue, liabilities, and equity read as positive, and a contra account shows as
  negative within its section: accumulated depreciation reduces assets, sales
  returns reduce revenue.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from opensumma.kernel.accounts import chart_of_accounts, descendants, get_account
from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.ledger import (
    LedgerLine,
    activity_before,
    ledger_lines,
    posted_activity,
)
from opensumma.kernel.models import Account
from opensumma.money import ZERO
from opensumma.utc import ensure_date


@dataclass(frozen=True)
class TrialBalanceLine:
    """One account's balance: positive for a debit balance, negative for a credit."""

    account_code: str
    account_name: str
    account_type: AccountType
    balance: Decimal


@dataclass(frozen=True)
class TrialBalance:
    """Every account with a balance on ``as_of``, and the total of the balances,
    which is zero whenever the ledger is intact."""

    as_of: date
    lines: tuple[TrialBalanceLine, ...]
    total: Decimal

    @property
    def is_balanced(self) -> bool:
        return self.total == ZERO


@dataclass(frozen=True)
class GeneralLedgerLine:
    """One posted line, with the account's balance after it."""

    entry_id: int
    entry_date: date
    line_number: int
    description: str
    memo: str | None
    amount: Decimal
    dimensions: tuple[tuple[str, str], ...]
    balance: Decimal


@dataclass(frozen=True)
class GeneralLedgerAccount:
    """One account's lines over a date range, between its opening and closing balance.

    Balances are signed like the lines: positive for a debit balance, negative for
    a credit balance.
    """

    account_code: str
    account_name: str
    normal_balance: NormalBalance
    opening_balance: Decimal
    lines: tuple[GeneralLedgerLine, ...]
    closing_balance: Decimal


@dataclass(frozen=True)
class GeneralLedger:
    """Every posted line from ``start`` to ``end``, account by account."""

    start: date
    end: date
    accounts: tuple[GeneralLedgerAccount, ...]


@dataclass(frozen=True)
class StatementLine:
    """One account on a financial statement.

    ``amount`` is in the section's normal direction; a parent's amount is the sum of
    the accounts below it. ``depth`` is 0 for a top-level account.
    """

    account_code: str
    account_name: str
    depth: int
    amount: Decimal
    is_leaf: bool


@dataclass(frozen=True)
class StatementSection:
    """The accounts of one type, as a hierarchy in chart order, and their total."""

    account_type: AccountType
    lines: tuple[StatementLine, ...]
    total: Decimal


@dataclass(frozen=True)
class IncomeStatement:
    """Revenue and expenses posted from ``start`` to ``end``, and the net income."""

    start: date
    end: date
    revenue: StatementSection
    expenses: StatementSection
    net_income: Decimal


@dataclass(frozen=True)
class BalanceSheet:
    """Assets, liabilities, and equity on ``as_of``.

    Revenue and expenses are never closed into an equity account yet, so all net
    income posted up to ``as_of`` belongs to the owners without appearing in any
    equity account. ``unclosed_net_income`` carries it into total equity; without
    it the sheet could not balance.
    """

    as_of: date
    assets: StatementSection
    liabilities: StatementSection
    equity: StatementSection
    unclosed_net_income: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal

    @property
    def is_balanced(self) -> bool:
        """Whether Assets = Liabilities + Equity, which double entry guarantees."""
        return self.assets.total == self.total_liabilities_and_equity


def trial_balance(session: Session, *, as_of: date) -> TrialBalance:
    """Each account's balance on ``as_of``, debit balances positive and credit
    balances negative.

    Accounts whose amounts cancel out are left off.
    """
    ensure_date(as_of)
    names = _accounts_by_code(session)
    lines = []
    for code, balance in sorted(posted_activity(session, end=as_of).items()):
        if balance == ZERO:
            continue
        account = names[code]
        lines.append(
            TrialBalanceLine(
                account_code=code,
                account_name=account.name,
                account_type=account.account_type,
                balance=balance,
            )
        )
    return TrialBalance(
        as_of=as_of,
        lines=tuple(lines),
        total=sum((line.balance for line in lines), ZERO),
    )


def general_ledger(
    session: Session, *, start: date, end: date, account_code: str | None = None
) -> GeneralLedger:
    """Every posted line dated ``start`` to ``end``, grouped by account.

    Without ``account_code``, every account with lines in the range or a balance
    brought into it is included. With one, only that account is, or every account
    below it when it is a parent, even those with no activity.
    """
    ensure_date(start)
    ensure_date(end)
    if account_code is None:
        lines = ledger_lines(session, start=start, end=end)
        opening = activity_before(session, start)
        codes = sorted(
            {line.account_code for line in lines}
            | {code for code, brought in opening.items() if brought != ZERO}
        )
    else:
        requested = get_account(session, account_code)
        codes = sorted(
            account.code
            for account in (requested, *descendants(requested))
            if account.is_leaf
        )
        lines = ledger_lines(session, start=start, end=end, account_codes=codes)
        opening = activity_before(session, start, account_codes=codes)

    lines_by_code: defaultdict[str, list[LedgerLine]] = defaultdict(list)
    for line in lines:
        lines_by_code[line.account_code].append(line)

    by_code = _accounts_by_code(session)
    accounts = []
    for code in codes:
        account = by_code[code]
        opening_balance = opening.get(code, ZERO)
        balance = opening_balance
        entries = []
        for line in lines_by_code[code]:
            balance += line.amount
            entries.append(
                GeneralLedgerLine(
                    entry_id=line.entry_id,
                    entry_date=line.entry_date,
                    line_number=line.line_number,
                    description=line.description,
                    memo=line.memo,
                    amount=line.amount,
                    dimensions=line.dimensions,
                    balance=balance,
                )
            )
        accounts.append(
            GeneralLedgerAccount(
                account_code=code,
                account_name=account.name,
                normal_balance=account.normal_balance,
                opening_balance=opening_balance,
                lines=tuple(entries),
                closing_balance=balance,
            )
        )
    return GeneralLedger(start=start, end=end, accounts=tuple(accounts))


def income_statement(session: Session, *, start: date, end: date) -> IncomeStatement:
    """Revenue and expenses posted from ``start`` to ``end`` inclusive."""
    ensure_date(start)
    ensure_date(end)
    chart = chart_of_accounts(session)
    activity = posted_activity(session, start=start, end=end)
    revenue = _section(AccountType.REVENUE, chart, activity)
    expenses = _section(AccountType.EXPENSE, chart, activity)
    return IncomeStatement(
        start=start,
        end=end,
        revenue=revenue,
        expenses=expenses,
        net_income=revenue.total - expenses.total,
    )


def balance_sheet(session: Session, *, as_of: date) -> BalanceSheet:
    """Assets, liabilities, and equity from everything posted up to ``as_of``."""
    ensure_date(as_of)
    chart = chart_of_accounts(session)
    activity = posted_activity(session, end=as_of)
    liabilities = _section(AccountType.LIABILITY, chart, activity)
    equity = _section(AccountType.EQUITY, chart, activity)
    unclosed_net_income = (
        _section(AccountType.REVENUE, chart, activity).total
        - _section(AccountType.EXPENSE, chart, activity).total
    )
    total_equity = equity.total + unclosed_net_income
    return BalanceSheet(
        as_of=as_of,
        assets=_section(AccountType.ASSET, chart, activity),
        liabilities=liabilities,
        equity=equity,
        unclosed_net_income=unclosed_net_income,
        total_equity=total_equity,
        total_liabilities_and_equity=liabilities.total + total_equity,
    )


def _section(
    account_type: AccountType, chart: list[Account], activity: dict[str, Decimal]
) -> StatementSection:
    """The accounts of ``account_type`` with their amounts rolled up the hierarchy,
    stated in the type's normal direction."""
    accounts = [account for account in chart if account.account_type is account_type]
    children: defaultdict[int | None, list[Account]] = defaultdict(list)
    for account in accounts:  # the chart is in code order, so each list is too
        children[account.parent_id].append(account)

    rolled_up: dict[int, Decimal] = {}

    def roll_up(account: Account) -> Decimal:
        total = activity.get(account.code, ZERO)
        for child in children[account.id]:
            total += roll_up(child)
        rolled_up[account.id] = total
        return total

    normal = account_type.normal_balance
    total = sum((roll_up(root) for root in children[None]), ZERO)

    lines: list[StatementLine] = []

    def visit(account: Account, depth: int) -> None:
        amount = _in_direction(rolled_up[account.id], normal)
        if amount == ZERO:
            return
        lines.append(
            StatementLine(
                account_code=account.code,
                account_name=account.name,
                depth=depth,
                amount=amount,
                is_leaf=not children[account.id],
            )
        )
        for child in children[account.id]:
            visit(child, depth + 1)

    for root in children[None]:
        visit(root, 0)
    return StatementSection(
        account_type=account_type,
        lines=tuple(lines),
        total=_in_direction(total, normal),
    )


def _in_direction(amount: Decimal, normal_balance: NormalBalance) -> Decimal:
    """``amount``, signed debits positive, stated on the ``normal_balance`` side."""
    if normal_balance is NormalBalance.DEBIT:
        return amount
    return ZERO - amount  # never -0.00


def _accounts_by_code(session: Session) -> dict[str, Account]:
    return {account.code: account for account in chart_of_accounts(session)}

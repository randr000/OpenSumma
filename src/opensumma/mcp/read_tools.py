"""The read-only tools. Each requires READ_ONLY and changes nothing.

They read the kernel and the object layer, as the REST interface's reads do, and
present records through the same views. A list is returned in a named field, such
as ``{"accounts": [...]}``, so every result is one JSON document, even an empty one.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field

from opensumma import kernel, objects
from opensumma.interface import schemas, views
from opensumma.mcp.context import ToolContext, reading
from opensumma.objects import (
    AccountingObjectStatus,
    AccountingObjectType,
    CounterpartyKind,
)
from opensumma.workflow import AuditResult, audit_history, get_actor


class Accounts(BaseModel):
    accounts: list[schemas.AccountOut]


class Periods(BaseModel):
    periods: list[schemas.PeriodOut]


class Dimensions(BaseModel):
    dimensions: list[schemas.DimensionOut]


class Counterparties(BaseModel):
    counterparties: list[schemas.CounterpartyOut]


class LedgerLines(BaseModel):
    lines: list[schemas.LedgerLineOut]


class AccountingObjects(BaseModel):
    accounting_objects: list[schemas.AccountingObjectOut]


class AuditEvents(BaseModel):
    audit_events: list[schemas.AuditEventOut]


def get_chart_of_accounts(ctx: ToolContext) -> Accounts:
    """The chart of accounts, by code. Only active accounts with no children are
    postable; parents summarize the accounts below them."""
    with reading(ctx) as session:
        found = kernel.chart_of_accounts(session)
        return Accounts(accounts=[views.account(a) for a in found])


def get_account(ctx: ToolContext, code: str) -> schemas.AccountOut:
    """One account, by its code, such as "6100"."""
    with reading(ctx) as session:
        return views.account(kernel.get_account(session, code))


def get_account_balance(
    ctx: ToolContext, code: str, as_of: date | None = None
) -> schemas.BalanceOut:
    """The posted balance of an account, and of the accounts below it, up to and
    including ``as_of`` (the whole ledger if omitted). The balance is signed:
    positive for a debit balance and negative for a credit balance, so a payable
    that is owed is negative."""
    with reading(ctx) as session:
        return views.balance(kernel.account_balance(session, code, as_of=as_of))


def get_accounting_periods(ctx: ToolContext) -> Periods:
    """Every accounting period, in order, and whether it is OPEN or CLOSED. An entry
    can be posted only into an open period."""
    with reading(ctx) as session:
        return Periods(periods=[views.period(p) for p in kernel.periods(session)])


def get_dimensions(ctx: ToolContext) -> Dimensions:
    """Every dimension, such as DEPARTMENT, and its values, which journal lines may
    carry."""
    with reading(ctx) as session:
        found = kernel.dimensions(session)
        return Dimensions(dimensions=[views.dimension(d) for d in found])


def get_counterparties(
    ctx: ToolContext, kind: CounterpartyKind | None = None
) -> Counterparties:
    """The vendors and customers, by code, optionally of one kind."""
    with reading(ctx) as session:
        found = objects.counterparties(session, kind=kind)
        return Counterparties(counterparties=[views.counterparty(c) for c in found])


def get_journal_entry(ctx: ToolContext, entry_id: int) -> schemas.JournalEntryOut:
    """One journal entry, in any status, with its lines and the accounting objects
    it records. Line amounts are signed, debits positive and credits negative, and
    ``total``, their sum, is zero when the entry balances."""
    with reading(ctx) as session:
        entry = kernel.get_journal_entry(session, entry_id)
        return views.journal_entry(session, entry)


def get_ledger(
    ctx: ToolContext,
    start: date | None = None,
    end: date | None = None,
    accounts: list[str] | None = None,
) -> LedgerLines:
    """Posted ledger lines by date, entry, and line, optionally for some account
    codes, between two dates, both inclusive. Only posted entries are in the
    ledger. Each line's amount is signed: a debit positive, a credit negative."""
    with reading(ctx) as session:
        found = kernel.ledger_lines(
            session, start=start, end=end, account_codes=accounts
        )
        return LedgerLines(lines=[views.ledger_line(line) for line in found])


def get_trial_balance(ctx: ToolContext, as_of: date) -> schemas.TrialBalanceOut:
    """The trial balance of the posted ledger as of a date: each account's balance,
    positive for a debit balance and negative for a credit balance, and their total,
    which is zero when the ledger balances."""
    with reading(ctx) as session:
        return views.trial_balance(kernel.trial_balance(session, as_of=as_of))


def get_income_statement(
    ctx: ToolContext, start: date, end: date
) -> schemas.IncomeStatementOut:
    """Revenue, expenses, and net income posted between two dates, both
    inclusive."""
    with reading(ctx) as session:
        report = kernel.income_statement(session, start=start, end=end)
        return views.income_statement(report)


def get_balance_sheet(ctx: ToolContext, as_of: date) -> schemas.BalanceSheetOut:
    """Assets, liabilities, and equity as of a date. Net income not yet closed into
    retained earnings is shown as ``unclosed_net_income``, within equity."""
    with reading(ctx) as session:
        return views.balance_sheet(kernel.balance_sheet(session, as_of=as_of))


def get_general_ledger(
    ctx: ToolContext, start: date, end: date, account: str | None = None
) -> schemas.GeneralLedgerOut:
    """Each account's opening balance, posted lines with a running balance, and
    closing balance between two dates, for every account or for one account code
    and the accounts below it. Amounts and balances are signed: debits positive,
    credits negative."""
    with reading(ctx) as session:
        report = kernel.general_ledger(
            session, start=start, end=end, account_code=account
        )
        return views.general_ledger(report)


def get_accounting_object(
    ctx: ToolContext, object_id: int
) -> schemas.AccountingObjectOut:
    """One accounting object, such as a vendor bill, with its business data,
    business events, and the journal entries that record it."""
    with reading(ctx) as session:
        found = objects.get_accounting_object(session, object_id)
        return views.accounting_object(session, found)


def search_accounting_objects(
    ctx: ToolContext,
    object_type: AccountingObjectType | None = None,
    status: AccountingObjectStatus | None = None,
    counterparty: str | None = None,
    source: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    data: dict[str, str] | None = None,
) -> AccountingObjects:
    """Accounting objects matching every filter given, by occurrence. ``data``
    matches top-level business-data fields exactly, such as
    ``{"invoice_number": "INV-2002"}``; times need a time zone."""
    with reading(ctx) as session:
        found = objects.search_accounting_objects(
            session,
            object_type=object_type,
            status=status,
            counterparty=counterparty,
            source=source,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            data=data,
        )
        return AccountingObjects(
            accounting_objects=[views.accounting_object(session, o) for o in found]
        )


def get_audit_history(
    ctx: ToolContext,
    object_type: str | None = None,
    object_id: int | None = None,
    actor: str | None = None,
    action: str | None = None,
    result: AuditResult | None = None,
    after: int | None = None,
    limit: Annotated[int, Field(ge=1, le=schemas.AUDIT_PAGE_LIMIT)] = 100,
) -> AuditEvents:
    """Audit events matching every filter, oldest first: who did what, with what
    input, reason, and evidence, and whether it SUCCEEDED or was REFUSED.

    ``object_type`` is a record's kind, such as ``journal_entry``; ``actor`` is an
    actor's code; ``action`` is a tool name, such as ``approve_journal_entry``.
    Pages hold at most ``limit`` events; pass the last ``sequence`` of a page as
    ``after`` to read the next.
    """
    with reading(ctx) as session:
        events = audit_history(
            session,
            object_type=object_type,
            object_id=object_id,
            actor=None if actor is None else get_actor(session, actor),
            action=action,
            result=result,
            after=after,
            limit=limit,
        )
        return AuditEvents(audit_events=[views.audit_event(e) for e in events])


TOOLS: list[Callable[..., Any]] = [
    get_chart_of_accounts,
    get_account,
    get_account_balance,
    get_accounting_periods,
    get_dimensions,
    get_counterparties,
    get_journal_entry,
    get_ledger,
    get_trial_balance,
    get_income_statement,
    get_balance_sheet,
    get_general_ledger,
    get_accounting_object,
    search_accounting_objects,
    get_audit_history,
]

"""Domain records as the interfaces present them.

Every REST endpoint and MCP tool that returns, say, a journal entry builds it here,
so an entry looks the same whichever endpoint or tool returned it.
"""

from collections.abc import Iterable, Sequence

from sqlalchemy.orm import Session

from opensumma.interface import schemas
from opensumma.kernel import (
    Account,
    AccountBalance,
    AccountingPeriod,
    BalanceSheet,
    Dimension,
    GeneralLedger,
    IncomeStatement,
    JournalEntry,
    LedgerLine,
    StatementSection,
    TrialBalance,
    ValidationIssue,
)
from opensumma.objects import (
    AccountingObject,
    Counterparty,
    accounting_impact,
    objects_for_journal_entry,
)
from opensumma.workflow import AuditEvent


def _pairs(dimensions: Iterable[tuple[str, str]]) -> dict[str, str]:
    return dict(dimensions)


def account(record: Account) -> schemas.AccountOut:
    return schemas.AccountOut(
        code=record.code,
        name=record.name,
        account_type=record.account_type.value,
        normal_balance=record.normal_balance.value,
        parent=None if record.parent is None else record.parent.code,
        is_active=record.is_active,
        is_postable=record.is_postable,
    )


def balance(record: AccountBalance) -> schemas.BalanceOut:
    return schemas.BalanceOut(
        account_code=record.account_code,
        account_name=record.account_name,
        account_type=record.account_type.value,
        normal_balance=record.normal_balance.value,
        as_of=record.as_of,
        balance=record.balance,
    )


def period(record: AccountingPeriod) -> schemas.PeriodOut:
    return schemas.PeriodOut(
        code=record.code,
        start_date=record.start_date,
        end_date=record.end_date,
        status=record.status.value,
    )


def dimension(record: Dimension) -> schemas.DimensionOut:
    return schemas.DimensionOut(
        code=record.code,
        name=record.name,
        values=[
            schemas.DimensionValueOut(code=v.code, name=v.name, is_active=v.is_active)
            for v in record.values
        ],
    )


def counterparty(record: Counterparty) -> schemas.CounterpartyOut:
    return schemas.CounterpartyOut(
        code=record.code,
        name=record.name,
        kind=record.kind.value,
        is_active=record.is_active,
    )


def journal_entry(session: Session, record: JournalEntry) -> schemas.JournalEntryOut:
    return schemas.JournalEntryOut(
        id=record.id,
        entry_date=record.entry_date,
        description=record.description,
        status=record.status.value,
        posted_at=record.posted_at,
        reversal_of=record.reversal_of_id,
        reversed_by=None if record.reversed_by is None else record.reversed_by.id,
        total=record.total,
        lines=[
            schemas.LineOut(
                line_number=line.line_number,
                account=line.account.code,
                amount=line.amount,
                memo=line.memo,
                dimensions=_pairs(
                    (tag.value.dimension.code, tag.value.code)
                    for tag in line.dimensions
                ),
            )
            for line in record.lines
        ],
        accounting_object_ids=[
            obj.id for obj in objects_for_journal_entry(session, record)
        ],
    )


def issues(found: Sequence[ValidationIssue]) -> list[schemas.IssueOut]:
    return [
        schemas.IssueOut(
            code=issue.code.value, line_number=issue.line_number, message=issue.message
        )
        for issue in found
    ]


def ledger_line(record: LedgerLine) -> schemas.LedgerLineOut:
    return schemas.LedgerLineOut(
        entry_id=record.entry_id,
        entry_date=record.entry_date,
        line_number=record.line_number,
        account_code=record.account_code,
        description=record.description,
        memo=record.memo,
        amount=record.amount,
        dimensions=_pairs(record.dimensions),
    )


def trial_balance(report: TrialBalance) -> schemas.TrialBalanceOut:
    return schemas.TrialBalanceOut(
        as_of=report.as_of,
        lines=[
            schemas.TrialBalanceLineOut(
                account_code=line.account_code,
                account_name=line.account_name,
                account_type=line.account_type.value,
                balance=line.balance,
            )
            for line in report.lines
        ],
        total=report.total,
        is_balanced=report.is_balanced,
    )


def _section(section: StatementSection) -> schemas.StatementSectionOut:
    return schemas.StatementSectionOut(
        account_type=section.account_type.value,
        lines=[
            schemas.StatementLineOut(
                account_code=line.account_code,
                account_name=line.account_name,
                depth=line.depth,
                amount=line.amount,
                is_leaf=line.is_leaf,
            )
            for line in section.lines
        ],
        total=section.total,
    )


def income_statement(report: IncomeStatement) -> schemas.IncomeStatementOut:
    return schemas.IncomeStatementOut(
        start=report.start,
        end=report.end,
        revenue=_section(report.revenue),
        expenses=_section(report.expenses),
        net_income=report.net_income,
    )


def balance_sheet(report: BalanceSheet) -> schemas.BalanceSheetOut:
    return schemas.BalanceSheetOut(
        as_of=report.as_of,
        assets=_section(report.assets),
        liabilities=_section(report.liabilities),
        equity=_section(report.equity),
        unclosed_net_income=report.unclosed_net_income,
        total_equity=report.total_equity,
        total_liabilities_and_equity=report.total_liabilities_and_equity,
        is_balanced=report.is_balanced,
    )


def general_ledger(report: GeneralLedger) -> schemas.GeneralLedgerOut:
    return schemas.GeneralLedgerOut(
        start=report.start,
        end=report.end,
        accounts=[
            schemas.GeneralLedgerAccountOut(
                account_code=acct.account_code,
                account_name=acct.account_name,
                normal_balance=acct.normal_balance.value,
                opening_balance=acct.opening_balance,
                lines=[
                    schemas.GeneralLedgerLineOut(
                        entry_id=line.entry_id,
                        entry_date=line.entry_date,
                        line_number=line.line_number,
                        description=line.description,
                        memo=line.memo,
                        amount=line.amount,
                        dimensions=_pairs(line.dimensions),
                        balance=line.balance,
                    )
                    for line in acct.lines
                ],
                closing_balance=acct.closing_balance,
            )
            for acct in report.accounts
        ],
    )


def accounting_object(
    session: Session, record: AccountingObject
) -> schemas.AccountingObjectOut:
    impact = accounting_impact(session, record)
    return schemas.AccountingObjectOut(
        id=record.id,
        object_type=record.object_type.value,
        status=record.status.value,
        occurred_at=record.occurred_at,
        source=record.source,
        counterparty=None if record.counterparty is None else record.counterparty.code,
        data=record.data,
        created_at=record.created_at,
        updated_at=record.updated_at,
        events=[
            schemas.BusinessEventOut(
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                source=event.source,
                data=event.data,
            )
            for event in record.events
        ],
        journal_entry_ids=[entry.entry_id for entry in impact.entries if entry.linked],
        has_net_impact=impact.has_net_impact,
    )


def audit_event(record: AuditEvent) -> schemas.AuditEventOut:
    return schemas.AuditEventOut(
        sequence=record.sequence,
        occurred_at=record.occurred_at,
        actor_type=record.actor_type.value,
        actor=None if record.actor is None else record.actor.code,
        action=record.action,
        object_type=record.object_type,
        object_id=record.object_id,
        input=record.input,
        output=record.output,
        result=record.result.value,
        reason=record.reason,
        evidence=record.evidence,
        hash=record.hash,
    )

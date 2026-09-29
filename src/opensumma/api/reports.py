"""Financial reports, derived from the posted ledger. Dates are always explicit."""

from datetime import date

from fastapi import APIRouter

from opensumma import kernel
from opensumma.api import schemas, views
from opensumma.api.dependencies import ReaderDep, SessionDep

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/trial-balance")
def trial_balance(
    as_of: date, session: SessionDep, reader: ReaderDep
) -> schemas.TrialBalanceOut:
    return views.trial_balance(kernel.trial_balance(session, as_of=as_of))


@router.get("/income-statement")
def income_statement(
    start: date, end: date, session: SessionDep, reader: ReaderDep
) -> schemas.IncomeStatementOut:
    return views.income_statement(
        kernel.income_statement(session, start=start, end=end)
    )


@router.get("/balance-sheet")
def balance_sheet(
    as_of: date, session: SessionDep, reader: ReaderDep
) -> schemas.BalanceSheetOut:
    return views.balance_sheet(kernel.balance_sheet(session, as_of=as_of))


@router.get("/general-ledger")
def general_ledger(
    start: date,
    end: date,
    session: SessionDep,
    reader: ReaderDep,
    account: str | None = None,
) -> schemas.GeneralLedgerOut:
    return views.general_ledger(
        kernel.general_ledger(session, start=start, end=end, account_code=account)
    )

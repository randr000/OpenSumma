"""Accounts, periods, dimensions, and counterparties."""

from datetime import date

from fastapi import APIRouter

from opensumma import kernel, workflow
from opensumma.api.dependencies import ActorDep, ReaderDep, SessionDep
from opensumma.interface import schemas, views
from opensumma.interface.work import unit_of_work
from opensumma.objects import CounterpartyKind, counterparties

router = APIRouter()


@router.get("/accounts", tags=["accounts"])
def list_accounts(session: SessionDep, reader: ReaderDep) -> list[schemas.AccountOut]:
    """The chart of accounts, by code."""
    return [views.account(a) for a in kernel.chart_of_accounts(session)]


@router.get("/accounts/{code}", tags=["accounts"])
def get_account(
    code: str, session: SessionDep, reader: ReaderDep
) -> schemas.AccountOut:
    """One account. Accounts are identified by their code, such as ``6100``."""
    return views.account(kernel.get_account(session, code))


@router.get("/accounts/{code}/balance", tags=["accounts"])
def get_account_balance(
    code: str, session: SessionDep, reader: ReaderDep, as_of: date | None = None
) -> schemas.BalanceOut:
    """The posted balance of an account, and of the accounts below it, up to
    ``as_of`` (the whole ledger if omitted), in its normal direction."""
    return views.balance(kernel.account_balance(session, code, as_of=as_of))


@router.get("/periods", tags=["periods"])
def list_periods(session: SessionDep, reader: ReaderDep) -> list[schemas.PeriodOut]:
    """Every accounting period, in order."""
    return [views.period(p) for p in kernel.periods(session)]


@router.post("/periods/{code}/close", tags=["periods"])
def close_period(
    code: str, session: SessionDep, actor: ActorDep, body: schemas.Action | None = None
) -> schemas.PeriodOut:
    """Close a period (ADMIN), after every earlier one, with nothing left to post."""
    body = body or schemas.Action()
    period = kernel.get_period(session, code)
    with unit_of_work(session):
        workflow.close_period(
            session, period, actor=actor, reason=body.reason, evidence=body.evidence
        )
    return views.period(period)


@router.post("/periods/{code}/reopen", tags=["periods"])
def reopen_period(
    code: str, session: SessionDep, actor: ActorDep, body: schemas.Action | None = None
) -> schemas.PeriodOut:
    """Reopen the latest closed period (ADMIN), with a reason."""
    body = body or schemas.Action()
    period = kernel.get_period(session, code)
    with unit_of_work(session):
        workflow.reopen_period(
            session,
            period,
            actor=actor,
            reason=body.reason or "",
            evidence=body.evidence,
        )
    return views.period(period)


@router.get("/dimensions", tags=["dimensions"])
def list_dimensions(
    session: SessionDep, reader: ReaderDep
) -> list[schemas.DimensionOut]:
    """Every dimension and its values, which journal lines may carry."""
    return [views.dimension(d) for d in kernel.dimensions(session)]


@router.get("/counterparties", tags=["counterparties"])
def list_counterparties(
    session: SessionDep, reader: ReaderDep, kind: CounterpartyKind | None = None
) -> list[schemas.CounterpartyOut]:
    """The vendors and customers, by code, optionally of one kind."""
    return [views.counterparty(c) for c in counterparties(session, kind=kind)]

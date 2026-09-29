"""Accounting objects through the workflow."""

from datetime import datetime

from fastapi import APIRouter

from opensumma import workflow
from opensumma.api.dependencies import ActorDep, ReaderDep, SessionDep
from opensumma.interface import schemas, views
from opensumma.interface.work import unit_of_work
from opensumma.objects import (
    AccountingObjectStatus,
    AccountingObjectType,
    get_accounting_object,
    search_accounting_objects,
)

router = APIRouter(prefix="/accounting-objects", tags=["accounting objects"])


@router.get("")
def list_accounting_objects(
    session: SessionDep,
    reader: ReaderDep,
    object_type: AccountingObjectType | None = None,
    status: AccountingObjectStatus | None = None,
    counterparty: str | None = None,
    source: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> list[schemas.AccountingObjectOut]:
    """Accounting objects matching every filter given, by occurrence."""
    found = search_accounting_objects(
        session,
        object_type=object_type,
        status=status,
        counterparty=counterparty,
        source=source,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return [views.accounting_object(session, obj) for obj in found]


@router.post("", status_code=201)
def observe_accounting_object(
    body: schemas.Observation, session: SessionDep, actor: ActorDep
) -> schemas.AccountingObjectOut:
    """Record a business document or event as it arrives (PROPOSER)."""
    with unit_of_work(session):
        obj = workflow.observe_accounting_object(
            session,
            actor=actor,
            object_type=body.object_type,
            occurred_at=body.occurred_at,
            source=body.source,
            counterparty=body.counterparty,
            data=body.data,
            reason=body.reason,
            evidence=body.evidence,
        )
    return views.accounting_object(session, obj)


@router.get("/{object_id}")
def get_object(
    object_id: int, session: SessionDep, reader: ReaderDep
) -> schemas.AccountingObjectOut:
    """One accounting object, with its business events and journal entries."""
    return views.accounting_object(session, get_accounting_object(session, object_id))


@router.post("/{object_id}/extract")
def extract_accounting_object(
    object_id: int, body: schemas.Extraction, session: SessionDep, actor: ActorDep
) -> schemas.AccountingObjectOut:
    """Replace the object's business data with what was read from its source."""
    obj = get_accounting_object(session, object_id)
    with unit_of_work(session):
        workflow.extract_accounting_object(
            session,
            obj,
            actor=actor,
            data=body.data,
            reason=body.reason,
            evidence=body.evidence,
        )
    return views.accounting_object(session, obj)


@router.post("/{object_id}/classify")
def classify_accounting_object(
    object_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Classification | None = None,
) -> schemas.AccountingObjectOut:
    """Settle the vendor or customer the object concerns (PROPOSER)."""
    body = body or schemas.Classification()
    obj = get_accounting_object(session, object_id)
    with unit_of_work(session):
        workflow.classify_accounting_object(
            session,
            obj,
            actor=actor,
            counterparty=body.counterparty,
            reason=body.reason,
            evidence=body.evidence,
        )
    return views.accounting_object(session, obj)


@router.post("/{object_id}/void")
def void_accounting_object(
    object_id: int,
    session: SessionDep,
    actor: ActorDep,
    body: schemas.Action | None = None,
) -> schemas.AccountingObjectOut:
    """Withdraw an object the ledger does not carry (APPROVER), with a reason."""
    body = body or schemas.Action()
    obj = get_accounting_object(session, object_id)
    with unit_of_work(session):
        workflow.void_accounting_object(
            session, obj, actor=actor, reason=body.reason or "", evidence=body.evidence
        )
    return views.accounting_object(session, obj)

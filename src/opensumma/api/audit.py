"""The audit log, read page by page."""

from typing import Annotated

from fastapi import APIRouter, Query

from opensumma.api.dependencies import ReaderDep, SessionDep
from opensumma.interface import schemas, views
from opensumma.workflow import AuditResult, audit_history, get_actor

router = APIRouter(tags=["audit"])


@router.get("/audit-events")
def list_audit_events(
    session: SessionDep,
    reader: ReaderDep,
    object_type: str | None = None,
    object_id: int | None = None,
    actor: str | None = None,
    action: str | None = None,
    result: AuditResult | None = None,
    after: int | None = None,
    limit: Annotated[int, Query(ge=1, le=schemas.AUDIT_PAGE_LIMIT)] = 100,
) -> list[schemas.AuditEventOut]:
    """Audit events matching every filter, oldest first.

    ``actor`` is an actor's code. Pages hold at most ``limit`` events; pass the last
    ``sequence`` of a page as ``after`` to read the next.
    """
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
    return [views.audit_event(event) for event in events]

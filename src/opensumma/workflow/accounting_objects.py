"""The accounting object workflow: observe, extract, classify, void.

These are the early stages of the accounting flow, where agents generally work: an
object is observed as it arrives, its business data is extracted from the source,
and it is classified by settling the counterparty it concerns. Its journal entries
then carry it through proposal, approval, and posting.
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from opensumma import objects
from opensumma.objects import (
    AccountingObject,
    AccountingObjectStatus,
    AccountingObjectType,
    assign_counterparty,
    create_accounting_object,
    ensure_business_data,
)
from opensumma.workflow.audit import AuditScope, audited
from opensumma.workflow.enums import WorkflowAction
from opensumma.workflow.errors import CounterpartyRequiredError
from opensumma.workflow.machine import (
    ACCOUNTING_OBJECT_WORKFLOW,
    Transition,
    authorize,
    reason_text,
    record_transition,
)
from opensumma.workflow.models import Actor


def observe_accounting_object(
    session: Session,
    *,
    actor: Actor,
    object_type: AccountingObjectType | str,
    occurred_at: datetime,
    source: str,
    counterparty: str | None = None,
    data: Mapping[str, Any] | None = None,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> AccountingObject:
    """Record a business document or event as it arrives, as OBSERVED."""
    with audited(
        session,
        actor=actor,
        action="observe_accounting_object",
        input={
            "object_type": object_type,
            "occurred_at": occurred_at,
            "source": source,
            "counterparty": counterparty,
            "data": data or {},
        },
        reason=reason,
        evidence=evidence,
    ) as audit:
        transition = authorize(
            ACCOUNTING_OBJECT_WORKFLOW, WorkflowAction.OBSERVE, None, actor
        )
        note = reason_text(reason)
        obj = create_accounting_object(
            session,
            object_type=object_type,
            occurred_at=occurred_at,
            source=source,
            counterparty=counterparty,
            data=data,
        )
        record_transition(
            session, obj, transition, actor=actor, from_status=None, reason=note
        )
        audit.subject = obj
        audit.output = {"status": obj.status}
    return obj


def extract_accounting_object(
    session: Session,
    obj: AccountingObject,
    *,
    actor: Actor,
    data: Mapping[str, Any],
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> None:
    """Replace ``obj``'s business data with what was read from its source.

    Extracting a classified object again sends it back to EXTRACTED, because its
    classification may no longer fit.
    """
    with _audited(
        session, "extract_accounting_object", obj, actor, reason, evidence, data=data
    ) as audit:
        transition = authorize(
            ACCOUNTING_OBJECT_WORKFLOW, WorkflowAction.EXTRACT, obj.status, actor
        )
        note = reason_text(reason)
        obj.data = ensure_business_data(data)
        _move(session, obj, transition, actor, note)
        audit.output = {"status": obj.status}


def classify_accounting_object(
    session: Session,
    obj: AccountingObject,
    *,
    actor: Actor,
    counterparty: str | None = None,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> None:
    """Settle whom ``obj`` concerns, and mark it CLASSIFIED.

    ``counterparty`` replaces the object's counterparty if given. An object whose
    type names a vendor or customer, such as a vendor bill, is classified only once
    it names one of the right kind.
    """
    with _audited(
        session,
        "classify_accounting_object",
        obj,
        actor,
        reason,
        evidence,
        counterparty=counterparty,
    ) as audit:
        transition = authorize(
            ACCOUNTING_OBJECT_WORKFLOW, WorkflowAction.CLASSIFY, obj.status, actor
        )
        note = reason_text(reason)
        if counterparty is not None:
            assign_counterparty(session, obj, counterparty)
        kind = obj.object_type.counterparty_kind
        if kind is not None and obj.counterparty is None:
            raise CounterpartyRequiredError(
                f"a {obj.object_type.value} is classified by naming its "
                f"{kind.value.lower()}"
            )
        _move(session, obj, transition, actor, note)
        audit.output = {
            "status": obj.status,
            "counterparty": None if obj.counterparty is None else obj.counterparty.code,
        }


def void_accounting_object(
    session: Session,
    obj: AccountingObject,
    *,
    actor: Actor,
    reason: str,
    evidence: Sequence[str] = (),
) -> None:
    """Withdraw ``obj``, such as a duplicate bill, saying why.

    The object layer refuses while the ledger still carries the object.
    """
    with _audited(
        session, "void_accounting_object", obj, actor, reason, evidence
    ) as audit:
        transition = authorize(
            ACCOUNTING_OBJECT_WORKFLOW, WorkflowAction.VOID, obj.status, actor
        )
        note = reason_text(reason, required_for="void an accounting object")
        before = obj.status
        objects.void_accounting_object(session, obj)
        record_transition(
            session, obj, transition, actor=actor, from_status=before, reason=note
        )
        audit.output = {"status": obj.status}


def _audited(
    session: Session,
    action: str,
    obj: AccountingObject,
    actor: Actor,
    reason: str | None,
    evidence: Sequence[str],
    **details: object,
) -> AbstractContextManager[AuditScope]:
    return audited(
        session,
        actor=actor,
        action=action,
        subject=obj,
        input={"accounting_object_id": obj.id, **details},
        reason=reason,
        evidence=evidence,
    )


def _move(
    session: Session,
    obj: AccountingObject,
    transition: Transition,
    actor: Actor,
    reason: str | None,
) -> None:
    before = obj.status
    obj.status = AccountingObjectStatus(transition.target)
    record_transition(
        session, obj, transition, actor=actor, from_status=before, reason=reason
    )

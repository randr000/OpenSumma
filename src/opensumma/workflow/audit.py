"""The audit log: every meaningful action, by whom, and what came of it.

Two kinds of record reach it:

- **Audited actions.** Every workflow operation runs inside ``audited``, which
  records one event: the actor, the action (named like the agent tool), its subject,
  input, output, reason, evidence, and whether it SUCCEEDED or was REFUSED. Refused
  attempts are recorded too, with the error and any issue codes, because what an
  actor tried and was stopped from doing matters as much as what it did.
- **Captured changes.** Any change to a record made outside an audited action, such
  as trusted code calling the kernel directly, is recorded by session hooks as an
  event by the SYSTEM with no actor: ``create_journal_entry``, ``update_account``,
  and so on, with the values before and after. Bulk writes, which would escape the
  hooks, are refused. Every accounting change is therefore in the log, whichever
  path made it; only raw SQL is beyond it.

Audit events are part of the caller's unit of work: committing persists them, and a
refused action changes nothing else, so its event is all a commit would add.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import Connection, event, inspect, select
from sqlalchemy.orm import (
    Mapper,
    ORMExecuteState,
    Session,
    UOWTransaction,
    object_session,
)
from sqlalchemy.sql.elements import NamedColumn

from opensumma.db import Base
from opensumma.kernel import JournalEntryError, KernelError, ValidationIssue
from opensumma.objects import AccountingObjectError, ensure_business_data
from opensumma.workflow.enums import ActorType, AuditResult
from opensumma.workflow.errors import UnauditedWriteError, WorkflowError
from opensumma.workflow.models import (
    ACTION_LENGTH,
    Actor,
    AuditEvent,
    audit_hash,
)

REASON_LENGTH = 500
EVIDENCE_LENGTH = 200
EVIDENCE_LIMIT = 50

# Exceptions that mean a rule refused the action, as opposed to a fault.
REFUSALS = (WorkflowError, KernelError, AccountingObjectError, ValueError, TypeError)

_ACTION = re.compile(r"[a-z][a-z0-9_]*")
_SCOPES = "opensumma.audit.scopes"
_PENDING = "opensumma.audit.pending_changes"
# The audit log and the workflow history are records themselves; timestamps are
# carried by every audit event anyway.
_UNCAPTURED_TABLES = frozenset({"audit_event", "workflow_transition"})
_UNCAPTURED_COLUMNS = frozenset({"created_at", "updated_at"})


# --- Values the audit log can hold ------------------------------------------------


def audit_json(value: object) -> Any:
    """``value`` as JSON the audit log stores exactly.

    Amounts become strings, dates and timestamps ISO text, enums their values, and
    dataclasses such as ``LineInput`` objects. A float, which business data would
    refuse, becomes its text: the log records what was attempted, even a float.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Enum):
        return audit_json(value.value)
    if isinstance(value, float | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: audit_json(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): audit_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [audit_json(item) for item in value]
    raise TypeError(f"the audit log cannot record a {type(value).__name__}")


def evidence_references(evidence: Sequence[str]) -> list[str]:
    """``evidence`` as a list of concise references, or raise.

    A reference names what supports the action, such as ``"vendor_id=42"``; prose
    belongs in the reason. There are at most 50, each at most 200 characters.
    """
    if isinstance(evidence, str):
        raise TypeError("evidence is a list of references, not a single string")
    references = []
    for item in evidence:
        if not isinstance(item, str):
            raise TypeError(f"an evidence reference is text, not {type(item).__name__}")
        text = item.strip()
        if not text:
            raise ValueError("an evidence reference must not be empty")
        if len(text) > EVIDENCE_LENGTH:
            raise ValueError(
                f"an evidence reference is limited to {EVIDENCE_LENGTH} characters"
            )
        references.append(text)
    if len(references) > EVIDENCE_LIMIT:
        raise ValueError(f"at most {EVIDENCE_LIMIT} evidence references")
    return references


def _concise(reason: str | None) -> str | None:
    """The reason as recorded: stripped, or None if empty or too long to be concise.

    An action whose reason is too long is itself refused; its event keeps no reason.
    """
    text = (reason or "").strip()
    return text if 0 < len(text) <= REASON_LENGTH else None


# --- Audited actions -----------------------------------------------------------------


@dataclass
class AuditScope:
    """An action in progress. The operation sets ``subject`` if it creates one, and
    ``output`` to describe what it did."""

    actor: Actor | None
    action: str
    subject: Base | None
    input: dict[str, Any]
    reason: str | None
    evidence: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)


@contextmanager
def audited(
    session: Session,
    *,
    actor: Actor | None,
    action: str,
    subject: Base | None = None,
    input: Mapping[str, Any] | None = None,
    reason: str | None = None,
    evidence: Sequence[str] = (),
) -> Iterator[AuditScope]:
    """Record the action taken inside this block as one audit event.

    If the block raises one of ``REFUSALS``, the event records the action as REFUSED,
    with the error, and the exception propagates. Otherwise the block's changes are
    flushed and the event records it as SUCCEEDED, with ``scope.output``. Changes
    made during the block are covered by this event and are not captured separately.

    ``actor`` is None only for trusted code acting as the system itself. Changes
    pending before the block are flushed first, so they are captured on their own
    rather than credited to this action.
    """
    kind = action.strip()
    if not _ACTION.fullmatch(kind) or len(kind) > ACTION_LENGTH:
        raise ValueError(f"an audited action is named in snake_case, got {action!r}")
    session.flush()
    scope = AuditScope(
        actor=actor,
        action=kind,
        subject=subject,
        input=ensure_business_data(audit_json(dict(input or {}))),
        reason=_concise(reason),
    )
    scopes: list[AuditScope] = session.info.setdefault(_SCOPES, [])
    scopes.append(scope)
    try:
        scope.evidence = evidence_references(evidence)
        yield scope
    except REFUSALS as error:
        _record(session, scope, AuditResult.REFUSED, _refusal(error))
        raise
    else:
        session.flush()
        _record(session, scope, AuditResult.SUCCEEDED, scope.output)
    finally:
        scopes.remove(scope)


def _record(
    session: Session, scope: AuditScope, result: AuditResult, output: Mapping[str, Any]
) -> None:
    subject, actor = scope.subject, scope.actor
    session.add(
        AuditEvent(
            actor_type=ActorType.SYSTEM if actor is None else actor.actor_type,
            actor=actor,
            action=scope.action,
            object_type=None if subject is None else _table_name(subject),
            object_id=None if subject is None else _first_key(subject),
            input=scope.input,
            output=ensure_business_data(audit_json(dict(output))),
            result=result,
            reason=scope.reason,
            evidence=scope.evidence,
        )
    )


def issues_json(issues: Sequence[ValidationIssue]) -> list[dict[str, Any]]:
    """Validation issues as the audit log records them: code, line, and message."""
    return [
        {"code": i.code.value, "line_number": i.line_number, "message": i.message}
        for i in issues
    ]


def _refusal(error: BaseException) -> dict[str, Any]:
    """What refused the action: the error, its message, and any details it names."""
    output: dict[str, Any] = {"error": type(error).__name__, "message": str(error)}
    if isinstance(error, JournalEntryError):
        output["issues"] = issues_json(error.issues)
    for detail in ("permission", "action", "status", "entry_ids", "period_codes"):
        value = getattr(error, detail, None)
        if value is not None:
            output[detail] = value
    return output


def _table_name(record: object) -> str:
    """The name of the table ``record`` is stored in, which names its kind."""
    return str(inspect(record, raiseerr=True).mapper.local_table.description)


def _first_key(record: object) -> int | None:
    """The record's id: for a composite key, its first part."""
    state = inspect(record, raiseerr=True)
    if state.identity is not None:
        key = state.identity[0]
    else:
        key = getattr(record, state.mapper.primary_key[0].key, None)
    return key if isinstance(key, int) else None


# --- Captured changes -----------------------------------------------------------------
#
# Captured from mapper events, which fire for every row the flush actually writes,
# including rows deleted as orphans by a cascade, in the order the flush writes them.
# The events are added once the flush is over.


def _captured(record: object) -> bool:
    return _table_name(record) not in _UNCAPTURED_TABLES


def _capturing(record: object) -> list[tuple[str, object, dict[str, Any]]] | None:
    """Where to note a change to ``record``, or None if it is not captured.

    Changes made during an audited action are covered by its own event.
    """
    session = object_session(record)
    if session is None or session.info.get(_SCOPES) or not _captured(record):
        return None
    pending: list[tuple[str, object, dict[str, Any]]] = session.info.setdefault(
        _PENDING, []
    )
    return pending


def _columns(mapper: Mapper[Any]) -> dict[str, NamedColumn[Any]]:
    """The captured columns, by attribute name."""
    return {
        prop.key: prop.columns[0]
        for prop in mapper.column_attrs
        if prop.key not in _UNCAPTURED_COLUMNS
    }


def _stored(
    mapper: Mapper[Any], connection: Connection, record: object, keys: list[str]
) -> dict[str, Any]:
    """The values of ``keys`` as the database holds them now, before this flush.

    Read from the database rather than from attribute history, which SQLAlchemy
    forgets once a commit has expired the record.
    """
    columns = _columns(mapper)
    key = mapper.primary_key_from_instance(record)
    where = [
        column == value for column, value in zip(mapper.primary_key, key, strict=True)
    ]
    row = connection.execute(select(*(columns[k] for k in keys)).where(*where)).one()
    return dict(zip(keys, row, strict=True))


@event.listens_for(Mapper, "after_insert")
def _capture_insert(
    mapper: Mapper[Any], connection: Connection, record: object
) -> None:
    if (pending := _capturing(record)) is not None:
        values = {k: audit_json(getattr(record, k)) for k in _columns(mapper)}
        pending.append(("create", record, {"values": values}))


@event.listens_for(Mapper, "before_update")
def _capture_update(
    mapper: Mapper[Any], connection: Connection, record: object
) -> None:
    if (pending := _capturing(record)) is None:
        return
    state = inspect(record, raiseerr=True)
    keys = [k for k in _columns(mapper) if state.attrs[k].history.has_changes()]
    if not keys:
        return
    before = _stored(mapper, connection, record, keys)
    changes = {
        k: {"from": audit_json(before[k]), "to": audit_json(getattr(record, k))}
        for k in keys
        if audit_json(before[k]) != audit_json(getattr(record, k))
    }
    if changes:
        pending.append(("update", record, {"changes": changes}))


@event.listens_for(Mapper, "before_delete")
def _capture_delete(
    mapper: Mapper[Any], connection: Connection, record: object
) -> None:
    if (pending := _capturing(record)) is not None:
        values = _stored(mapper, connection, record, list(_columns(mapper)))
        pending.append(
            (
                "delete",
                record,
                {"values": {k: audit_json(v) for k, v in values.items()}},
            )
        )


@event.listens_for(Session, "before_flush")
def _start_capture(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    session.info[_PENDING] = []  # nothing left over from a flush that failed


@event.listens_for(Session, "after_flush_postexec")
def _record_changes(session: Session, flush_context: UOWTransaction) -> None:
    for kind, record, detail in session.info.pop(_PENDING, []):
        table = _table_name(record)
        session.add(
            AuditEvent(
                actor_type=ActorType.SYSTEM,
                action=f"{kind}_{table}",
                object_type=table,
                object_id=_first_key(record),
                input=detail,
                result=AuditResult.SUCCEEDED,
            )
        )


@event.listens_for(Session, "do_orm_execute")
def _refuse_unaudited_bulk_writes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.name in Base.metadata.tables:
        raise UnauditedWriteError(
            f"bulk writes to {table.name} are refused, because they would change "
            "records without an audit event; write through the session instead"
        )


# --- Reading and verifying the log --------------------------------------------------


def audit_history(
    session: Session,
    *,
    subject: Base | None = None,
    actor: Actor | None = None,
    action: str | None = None,
    result: AuditResult | str | None = None,
) -> list[AuditEvent]:
    """Audit events matching every filter given, oldest first."""
    session.flush()
    statement = select(AuditEvent).order_by(AuditEvent.sequence)
    if subject is not None:
        statement = statement.where(
            AuditEvent.object_type == _table_name(subject),
            AuditEvent.object_id == _first_key(subject),
        )
    if actor is not None:
        statement = statement.where(AuditEvent.actor_id == actor.id)
    if action is not None:
        statement = statement.where(AuditEvent.action == action)
    if result is not None:
        statement = statement.where(AuditEvent.result == AuditResult(result))
    return list(session.scalars(statement))


@dataclass(frozen=True)
class AuditVerification:
    """The outcome of checking the audit log's chain.

    ``head_hash`` is the hash of the last event. Recording it elsewhere lets a later
    check notice events removed from the end, which the chain alone cannot show.
    """

    events_checked: int
    broken_at: int | None
    problem: str | None
    head_hash: str | None

    @property
    def is_intact(self) -> bool:
        return self.broken_at is None


def verify_audit_log(session: Session) -> AuditVerification:
    """Check that no audit event was changed, removed, or inserted out of turn.

    Every event must follow the one before it in sequence, carry that event's hash,
    and hash to the value it records. The first event that does not is reported.
    """
    session.flush()
    previous: str | None = None
    checked = 0
    for record in session.scalars(select(AuditEvent).order_by(AuditEvent.sequence)):
        expected = checked + 1
        problem = None
        if record.sequence != expected:
            problem = f"expected event {expected}, found event {record.sequence}"
        elif record.previous_hash != previous:
            problem = "it does not carry the hash of the event before it"
        elif audit_hash(record) != record.hash:
            problem = "its content does not match its hash"
        if problem is not None:
            return AuditVerification(checked, expected, problem, previous)
        previous = record.hash
        checked += 1
    return AuditVerification(checked, None, None, previous)

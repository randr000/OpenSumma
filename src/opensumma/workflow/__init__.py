"""The workflow engine: who may do what to journal entries, objects, and periods,
and the audit log of everything that was done.

Every accounting subject moves through a state machine (``machine``), and every
move is taken by an ``Actor`` (a person, an AI agent, or the system) holding the
permission the move needs. Each move is recorded in an append-only history, and
every action, allowed or refused, in the hash-chained audit log (``audit``), which
also captures changes made outside the workflow.

The operations here carry the names of the agent tools that will invoke them, and
wrap the kernel and object-layer operations of the same name with state,
permission, and control checks. The layers beneath remain usable by trusted code;
every interface an actor can reach goes through this one.

This layer depends on the kernel and the object layer; neither depends on it.
Importing it registers its persistence models on ``Base.metadata`` and the session
hooks that keep its history append-only.
"""

from opensumma.workflow.accounting_objects import (
    classify_accounting_object,
    extract_accounting_object,
    observe_accounting_object,
    void_accounting_object,
)
from opensumma.workflow.actors import (
    actors,
    authenticate,
    create_actor,
    deactivate_actor,
    find_actor,
    get_actor,
    issue_api_key,
    require_permission,
    revoke_api_keys,
    set_actor_permissions,
)
from opensumma.workflow.audit import (
    AuditScope,
    AuditVerification,
    audit_history,
    audit_json,
    audited,
    describe_refusal,
    evidence_references,
    verify_audit_log,
)
from opensumma.workflow.entries import (
    approve_journal_entry,
    post_journal_entry,
    propose_journal_entry,
    reject_journal_entry,
    reverse_journal_entry,
    submit_for_approval,
    validate_journal_entry,
    void_journal_entry,
)
from opensumma.workflow.enums import (
    ActorType,
    AuditResult,
    Permission,
    WorkflowAction,
)
from opensumma.workflow.errors import (
    AuthenticationError,
    CounterpartyRequiredError,
    ImmutableHistoryError,
    InvalidTransitionError,
    PendingEntriesError,
    PeriodSequenceError,
    PermissionDeniedError,
    SegregationOfDutiesError,
    UnauditedWriteError,
    UnknownActorError,
    WorkflowError,
)
from opensumma.workflow.machine import (
    ACCOUNTING_OBJECT_WORKFLOW,
    ACCOUNTING_PERIOD_WORKFLOW,
    JOURNAL_ENTRY_WORKFLOW,
    Transition,
    workflow_history,
)
from opensumma.workflow.models import (
    Actor,
    ActorPermission,
    ApiKey,
    AuditEvent,
    WorkflowTransition,
    audit_hash,
)
from opensumma.workflow.periods import close_period, reopen_period

__all__ = [
    "ACCOUNTING_OBJECT_WORKFLOW",
    "ACCOUNTING_PERIOD_WORKFLOW",
    "JOURNAL_ENTRY_WORKFLOW",
    "Actor",
    "ActorPermission",
    "ActorType",
    "ApiKey",
    "AuditEvent",
    "AuditResult",
    "AuditScope",
    "AuditVerification",
    "AuthenticationError",
    "CounterpartyRequiredError",
    "ImmutableHistoryError",
    "InvalidTransitionError",
    "PendingEntriesError",
    "PeriodSequenceError",
    "Permission",
    "PermissionDeniedError",
    "SegregationOfDutiesError",
    "Transition",
    "UnauditedWriteError",
    "UnknownActorError",
    "WorkflowAction",
    "WorkflowError",
    "WorkflowTransition",
    "actors",
    "approve_journal_entry",
    "audit_hash",
    "audit_history",
    "audit_json",
    "audited",
    "authenticate",
    "classify_accounting_object",
    "close_period",
    "create_actor",
    "deactivate_actor",
    "describe_refusal",
    "evidence_references",
    "extract_accounting_object",
    "find_actor",
    "get_actor",
    "issue_api_key",
    "observe_accounting_object",
    "post_journal_entry",
    "propose_journal_entry",
    "reject_journal_entry",
    "reopen_period",
    "require_permission",
    "reverse_journal_entry",
    "revoke_api_keys",
    "set_actor_permissions",
    "submit_for_approval",
    "validate_journal_entry",
    "verify_audit_log",
    "void_accounting_object",
    "void_journal_entry",
    "workflow_history",
]

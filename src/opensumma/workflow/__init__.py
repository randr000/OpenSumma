"""The workflow engine: who may do what to journal entries, objects, and periods.

Every accounting subject moves through a state machine (``machine``), and every
move is taken by an ``Actor`` (a person, an AI agent, or the system) holding the
permission the move needs. Each move is recorded in an append-only history.

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
    create_actor,
    deactivate_actor,
    find_actor,
    get_actor,
    require_permission,
    set_actor_permissions,
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
from opensumma.workflow.enums import ActorType, Permission, WorkflowAction
from opensumma.workflow.errors import (
    CounterpartyRequiredError,
    ImmutableHistoryError,
    InvalidTransitionError,
    PendingEntriesError,
    PeriodSequenceError,
    PermissionDeniedError,
    SegregationOfDutiesError,
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
from opensumma.workflow.models import Actor, ActorPermission, WorkflowTransition
from opensumma.workflow.periods import close_period, reopen_period

__all__ = [
    "ACCOUNTING_OBJECT_WORKFLOW",
    "ACCOUNTING_PERIOD_WORKFLOW",
    "JOURNAL_ENTRY_WORKFLOW",
    "Actor",
    "ActorPermission",
    "ActorType",
    "CounterpartyRequiredError",
    "ImmutableHistoryError",
    "InvalidTransitionError",
    "PendingEntriesError",
    "PeriodSequenceError",
    "Permission",
    "PermissionDeniedError",
    "SegregationOfDutiesError",
    "Transition",
    "UnknownActorError",
    "WorkflowAction",
    "WorkflowError",
    "WorkflowTransition",
    "actors",
    "approve_journal_entry",
    "classify_accounting_object",
    "close_period",
    "create_actor",
    "deactivate_actor",
    "extract_accounting_object",
    "find_actor",
    "get_actor",
    "observe_accounting_object",
    "post_journal_entry",
    "propose_journal_entry",
    "reject_journal_entry",
    "reopen_period",
    "require_permission",
    "reverse_journal_entry",
    "set_actor_permissions",
    "submit_for_approval",
    "validate_journal_entry",
    "void_accounting_object",
    "void_journal_entry",
    "workflow_history",
]

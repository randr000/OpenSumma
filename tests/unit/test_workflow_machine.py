"""The workflow state machines, checked as tables: no database needed."""

from enum import StrEnum

import pytest

from opensumma.kernel import JournalEntryStatus, PeriodStatus
from opensumma.objects import AccountingObjectStatus
from opensumma.workflow import (
    ACCOUNTING_OBJECT_WORKFLOW,
    ACCOUNTING_PERIOD_WORKFLOW,
    JOURNAL_ENTRY_WORKFLOW,
    Actor,
    ActorPermission,
    ActorType,
    InvalidTransitionError,
    Permission,
    PermissionDeniedError,
    Transition,
    WorkflowAction,
)
from opensumma.workflow.machine import authorize

Entry = JournalEntryStatus
Object = AccountingObjectStatus
Action = WorkflowAction


def _actor(*permissions: Permission, active: bool = True) -> Actor:
    return Actor(
        code="tester",
        name="Tester",
        actor_type=ActorType.HUMAN,
        is_active=active,
        grants=[ActorPermission(permission=permission) for permission in permissions],
    )


EVERYONE = _actor(*Permission)

MOVES = [
    (JOURNAL_ENTRY_WORKFLOW, Action.PROPOSE, None, Entry.PROPOSED, Permission.PROPOSER),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.SUBMIT,
        Entry.DRAFT,
        Entry.PENDING_APPROVAL,
        Permission.PROPOSER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.SUBMIT,
        Entry.PROPOSED,
        Entry.PENDING_APPROVAL,
        Permission.PROPOSER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.APPROVE,
        Entry.PENDING_APPROVAL,
        Entry.APPROVED,
        Permission.APPROVER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.REJECT,
        Entry.PENDING_APPROVAL,
        Entry.PROPOSED,
        Permission.APPROVER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.POST,
        Entry.APPROVED,
        Entry.POSTED,
        Permission.POSTER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.REVERSE,
        Entry.POSTED,
        Entry.REVERSED,
        Permission.POSTER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.VOID,
        Entry.DRAFT,
        Entry.VOIDED,
        Permission.PROPOSER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.VOID,
        Entry.PROPOSED,
        Entry.VOIDED,
        Permission.PROPOSER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.VOID,
        Entry.PENDING_APPROVAL,
        Entry.VOIDED,
        Permission.APPROVER,
    ),
    (
        JOURNAL_ENTRY_WORKFLOW,
        Action.VOID,
        Entry.APPROVED,
        Entry.VOIDED,
        Permission.APPROVER,
    ),
    (
        ACCOUNTING_OBJECT_WORKFLOW,
        Action.OBSERVE,
        None,
        Object.OBSERVED,
        Permission.PROPOSER,
    ),
    (
        ACCOUNTING_OBJECT_WORKFLOW,
        Action.EXTRACT,
        Object.CLASSIFIED,
        Object.EXTRACTED,
        Permission.PROPOSER,
    ),
    (
        ACCOUNTING_OBJECT_WORKFLOW,
        Action.CLASSIFY,
        Object.OBSERVED,
        Object.CLASSIFIED,
        Permission.PROPOSER,
    ),
    (
        ACCOUNTING_OBJECT_WORKFLOW,
        Action.VOID,
        Object.EXTRACTED,
        Object.VOIDED,
        Permission.APPROVER,
    ),
    (
        ACCOUNTING_PERIOD_WORKFLOW,
        Action.CLOSE,
        PeriodStatus.OPEN,
        PeriodStatus.CLOSED,
        Permission.ADMIN,
    ),
    (
        ACCOUNTING_PERIOD_WORKFLOW,
        Action.REOPEN,
        PeriodStatus.CLOSED,
        PeriodStatus.OPEN,
        Permission.ADMIN,
    ),
]


@pytest.mark.parametrize(
    ("workflow", "action", "source", "target", "permission"), MOVES
)
def test_each_move_needs_exactly_its_permission(
    workflow: tuple[Transition, ...],
    action: WorkflowAction,
    source: StrEnum | None,
    target: StrEnum,
    permission: Permission,
) -> None:
    assert authorize(workflow, action, source, _actor(permission)).target is target

    everything_else = _actor(*(p for p in Permission if p is not permission))
    with pytest.raises(PermissionDeniedError, match=permission.value) as caught:
        authorize(workflow, action, source, everything_else)
    assert caught.value.permission is permission


@pytest.mark.parametrize("status", [s for s in Entry if s is not Entry.APPROVED])
def test_an_entry_is_posted_only_once_approved(status: JournalEntryStatus) -> None:
    with pytest.raises(InvalidTransitionError, match="allowed from APPROVED"):
        authorize(JOURNAL_ENTRY_WORKFLOW, Action.POST, status, EVERYONE)


@pytest.mark.parametrize(
    "status", [s for s in Entry if s is not Entry.PENDING_APPROVAL]
)
def test_an_entry_is_approved_only_while_pending(status: JournalEntryStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        authorize(JOURNAL_ENTRY_WORKFLOW, Action.APPROVE, status, EVERYONE)


@pytest.mark.parametrize(
    ("workflow", "final"),
    [
        (JOURNAL_ENTRY_WORKFLOW, Entry.VOIDED),
        (JOURNAL_ENTRY_WORKFLOW, Entry.REVERSED),
        (ACCOUNTING_OBJECT_WORKFLOW, Object.VOIDED),
    ],
)
def test_voided_and_reversed_are_dead_ends(
    workflow: tuple[Transition, ...], final: StrEnum
) -> None:
    assert all(final not in transition.sources for transition in workflow)


@pytest.mark.parametrize(
    ("workflow", "statuses"),
    [
        (JOURNAL_ENTRY_WORKFLOW, JournalEntryStatus),
        (ACCOUNTING_OBJECT_WORKFLOW, AccountingObjectStatus),
        (ACCOUNTING_PERIOD_WORKFLOW, PeriodStatus),
    ],
)
def test_every_status_takes_part_in_its_workflow(
    workflow: tuple[Transition, ...], statuses: type[StrEnum]
) -> None:
    mentioned = {t.target for t in workflow} | {
        source for t in workflow for source in t.sources if source is not None
    }
    assert mentioned == set(statuses)


def test_the_state_is_checked_before_the_permission() -> None:
    with pytest.raises(InvalidTransitionError, match="cannot post from DRAFT"):
        authorize(JOURNAL_ENTRY_WORKFLOW, Action.POST, Entry.DRAFT, _actor())


def test_a_refusal_names_the_states_the_action_is_allowed_from() -> None:
    with pytest.raises(InvalidTransitionError) as caught:
        authorize(JOURNAL_ENTRY_WORKFLOW, Action.SUBMIT, Entry.APPROVED, EVERYONE)
    assert str(caught.value) == (
        "cannot submit from APPROVED; allowed from DRAFT, PROPOSED"
    )
    assert caught.value.action is Action.SUBMIT
    assert caught.value.status is Entry.APPROVED


def test_an_inactive_actor_cannot_act() -> None:
    with pytest.raises(PermissionDeniedError, match="inactive"):
        authorize(
            JOURNAL_ENTRY_WORKFLOW,
            Action.PROPOSE,
            None,
            _actor(Permission.PROPOSER, active=False),
        )


def test_permissions_are_independent() -> None:
    admin = _actor(Permission.ADMIN)
    for action, source in [
        (Action.APPROVE, Entry.PENDING_APPROVAL),
        (Action.POST, Entry.APPROVED),
    ]:
        with pytest.raises(PermissionDeniedError):
            authorize(JOURNAL_ENTRY_WORKFLOW, action, source, admin)

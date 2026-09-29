"""Actor services: the people, agents, and processes that act through the workflow.

Creating actors and setting their permissions is trusted setup, done in Python like
seeding a chart of accounts; no workflow action grants permissions.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma.kernel import DuplicateCodeError
from opensumma.workflow.enums import ActorType, Permission
from opensumma.workflow.errors import PermissionDeniedError, UnknownActorError
from opensumma.workflow.models import Actor, ActorPermission


def create_actor(
    session: Session,
    *,
    code: str,
    name: str,
    actor_type: ActorType | str,
    permissions: Iterable[Permission | str] = (),
) -> Actor:
    """Register an actor with exactly the permissions given."""
    code = code.strip()
    if not code:
        raise ValueError("actor code must not be empty")
    if find_actor(session, code) is not None:
        raise DuplicateCodeError(f"actor code {code!r} already exists")
    actor = Actor(code=code, name=name, actor_type=ActorType(actor_type))
    set_actor_permissions(actor, permissions)
    session.add(actor)
    return actor


def find_actor(session: Session, code: str) -> Actor | None:
    """Return the actor with ``code``, or ``None``."""
    return session.scalars(select(Actor).where(Actor.code == code)).one_or_none()


def get_actor(session: Session, code: str) -> Actor:
    """Return the actor with ``code``, or raise."""
    actor = find_actor(session, code)
    if actor is None:
        raise UnknownActorError(f"no actor with code {code!r}")
    return actor


def actors(session: Session) -> list[Actor]:
    """Every actor, ordered by code."""
    return list(session.scalars(select(Actor).order_by(Actor.code)))


def set_actor_permissions(
    actor: Actor, permissions: Iterable[Permission | str]
) -> None:
    """Replace the actor's permissions with exactly ``permissions``."""
    wanted = {Permission(permission) for permission in permissions}
    actor.grants = [grant for grant in actor.grants if grant.permission in wanted] + [
        ActorPermission(permission=permission)
        for permission in sorted(wanted - actor.permissions)
    ]


def deactivate_actor(actor: Actor) -> None:
    """Stop an actor from acting. Its past transitions stay on record."""
    actor.is_active = False


def require_permission(actor: Actor, permission: Permission) -> None:
    """Raise unless ``actor`` is active and holds ``permission``."""
    if not actor.is_active:
        raise PermissionDeniedError(f"actor {actor.code} is inactive", permission)
    if permission not in actor.permissions:
        raise PermissionDeniedError(
            f"actor {actor.code} ({actor.actor_type.value}) lacks the "
            f"{permission.value} permission",
            permission,
        )

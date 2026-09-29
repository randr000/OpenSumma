"""What every tool call needs: a session and the calling actor.

A server acts as one actor: the one whose API key it was started with. The key is
checked on every call, as the REST interface checks it on every request, so
revoking it or deactivating the actor cuts a running server off at once.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context
from sqlalchemy.orm import Session, sessionmaker

from opensumma.workflow import Actor, Permission, authenticate, require_permission


@dataclass(frozen=True)
class ServerState:
    """What a running server holds: sessions on its database, and its actor's key."""

    sessions: sessionmaker[Session]
    api_key: str


ToolContext = Context[ServerState, Any]


@contextmanager
def acting(ctx: ToolContext) -> Iterator[tuple[Session, Actor]]:
    """A session for this call, closed, and so rolled back, when it ends, and the
    actor the server's key identifies."""
    state = ctx.request_context.lifespan_context
    with state.sessions() as session:
        yield session, authenticate(session, state.api_key)


@contextmanager
def reading(ctx: ToolContext) -> Iterator[Session]:
    """A session for a read, by an actor who must hold READ_ONLY."""
    with acting(ctx) as (session, actor):
        require_permission(actor, Permission.READ_ONLY)
        yield session

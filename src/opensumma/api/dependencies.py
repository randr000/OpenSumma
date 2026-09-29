"""What every request needs: a session and the calling actor.

The unit of work around an action is shared with the MCP interface, in
``opensumma.interface.work``.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from opensumma.workflow import (
    Actor,
    AuthenticationError,
    Permission,
    authenticate,
    require_permission,
)

_bearer = HTTPBearer(auto_error=False, description="An actor's API key")


def get_session(request: Request) -> Iterator[Session]:
    """A session for this request, closed, and so rolled back, when it ends."""
    sessions: sessionmaker[Session] = request.app.state.sessions
    with sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_actor(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Actor:
    """The actor whose API key the request carries, as ``Authorization: Bearer``."""
    if credentials is None:
        raise AuthenticationError(
            "an API key is required, as the header 'Authorization: Bearer <key>'"
        )
    return authenticate(session, credentials.credentials)


ActorDep = Annotated[Actor, Depends(get_actor)]


def get_reader(actor: ActorDep) -> Actor:
    """The calling actor, who must hold READ_ONLY to read anything."""
    require_permission(actor, Permission.READ_ONLY)
    return actor


ReaderDep = Annotated[Actor, Depends(get_reader)]

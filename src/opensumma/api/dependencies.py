"""What every request needs: a session, the calling actor, and a unit of work."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from opensumma.workflow import (
    Actor,
    AuthenticationError,
    Permission,
    authenticate,
    require_permission,
)
from opensumma.workflow.audit import REFUSALS

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


@contextmanager
def unit_of_work(session: Session) -> Iterator[None]:
    """Commit what an action did, or, if a rule refused it, its audit event.

    A refused action changes nothing but the audit log, so committing keeps the
    record of the attempt without keeping anything else. Any other failure rolls
    everything back.
    """
    try:
        yield
    except REFUSALS:
        try:
            session.commit()
        except SQLAlchemyError:  # the refusal came from a failed flush
            session.rollback()
        raise
    except BaseException:
        session.rollback()
        raise
    else:
        session.commit()

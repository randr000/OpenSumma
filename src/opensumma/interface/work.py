"""The unit of work of one action taken through an interface."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from opensumma.workflow.audit import REFUSALS


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

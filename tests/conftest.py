from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import opensumma.workflow  # noqa: F401  (registers every model on Base.metadata)
from opensumma.db import Base, create_engine
from opensumma.kernel import (
    create_calendar_year_periods,
    seed_chart_of_accounts,
    seed_dimensions,
)
from opensumma.objects import seed_counterparties
from opensumma.workflow import Actor, ActorType, Permission, create_actor


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """URL of a fresh, empty SQLite database file unique to the test."""
    return f"sqlite:///{tmp_path / 'opensumma.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session() -> Iterator[Session]:
    """A session on an empty in-memory database with the current schema.

    The schema comes from the models rather than from Alembic, and lives in memory
    rather than in a file, because both are far faster per test: every DDL
    statement on a file waits for the disk. ``test_models_match_migrations`` proves
    models and migrations agree, and acceptance tests go through ``init_db`` on a
    real file instead.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def books(session: Session) -> Session:
    """A session whose books are open: the default chart, dimensions, and
    counterparties, and the periods of 2026."""
    seed_chart_of_accounts(session)
    seed_dimensions(session)
    seed_counterparties(session)
    create_calendar_year_periods(session, 2026)
    session.commit()
    return session


# A cast of workflow actors on open books. The agent and the clerk prepare entries;
# the controller approves them; the poster posts them; the admin closes periods.
# Each holds only what the role needs.


def _actor(
    session: Session, code: str, actor_type: ActorType, *permissions: Permission
) -> Actor:
    actor = create_actor(
        session,
        code=code,
        name=code.title(),
        actor_type=actor_type,
        permissions=permissions,
    )
    session.flush()
    return actor


@pytest.fixture
def agent(books: Session) -> Actor:
    """A normal AI accounting agent: it may read and propose, nothing more."""
    return _actor(
        books, "je-agent", ActorType.AGENT, Permission.READ_ONLY, Permission.PROPOSER
    )


@pytest.fixture
def clerk(books: Session) -> Actor:
    return _actor(
        books, "clerk", ActorType.HUMAN, Permission.READ_ONLY, Permission.PROPOSER
    )


@pytest.fixture
def controller(books: Session) -> Actor:
    return _actor(
        books, "controller", ActorType.HUMAN, Permission.READ_ONLY, Permission.APPROVER
    )


@pytest.fixture
def poster(books: Session) -> Actor:
    return _actor(books, "poster", ActorType.SYSTEM, Permission.POSTER)


@pytest.fixture
def admin(books: Session) -> Actor:
    return _actor(books, "admin", ActorType.HUMAN, Permission.ADMIN)

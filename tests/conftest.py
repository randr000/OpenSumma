import shutil
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from anyio.from_thread import BlockingPortal, start_blocking_portal
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult, Tool
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import opensumma.workflow  # noqa: F401  (registers every model on Base.metadata)
from opensumma.api import create_app
from opensumma.db import Base, create_engine, init_db
from opensumma.kernel import (
    create_calendar_year_periods,
    seed_chart_of_accounts,
    seed_dimensions,
)
from opensumma.mcp import create_server
from opensumma.objects import seed_counterparties
from opensumma.workflow import (
    Actor,
    ActorType,
    Permission,
    create_actor,
    issue_api_key,
)


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


# --- The REST interface ------------------------------------------------------------
#
# A migrated database with open books, counterparties, and one API key per actor is
# built once and copied for every test, because migrating a file takes a while.

API_CAST: dict[str, tuple[ActorType, tuple[Permission, ...]]] = {
    "reader": (ActorType.AGENT, (Permission.READ_ONLY,)),
    "agent": (ActorType.AGENT, (Permission.READ_ONLY, Permission.PROPOSER)),
    "clerk": (ActorType.HUMAN, (Permission.READ_ONLY, Permission.PROPOSER)),
    "controller": (ActorType.HUMAN, (Permission.READ_ONLY, Permission.APPROVER)),
    "poster": (ActorType.SYSTEM, (Permission.READ_ONLY, Permission.POSTER)),
    "admin": (ActorType.HUMAN, (Permission.READ_ONLY, Permission.ADMIN)),
    "nobody": (ActorType.AGENT, ()),
}


@pytest.fixture(scope="session")
def api_template(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, dict[str, str]]:
    path = tmp_path_factory.mktemp("api") / "template.db"
    url = f"sqlite:///{path}"
    init_db(url)
    engine = create_engine(url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        seed_counterparties(session)
        create_calendar_year_periods(session, 2026)
        keys = {}
        for code, (actor_type, permissions) in API_CAST.items():
            actor = create_actor(
                session,
                code=code,
                name=code.title(),
                actor_type=actor_type,
                permissions=permissions,
            )
            keys[code] = issue_api_key(session, actor)
        session.commit()
    engine.dispose()
    return path, keys


@pytest.fixture
def api(
    api_template: tuple[Path, dict[str, str]], tmp_path: Path
) -> Iterator[TestClient]:
    """A client for the REST interface over a fresh copy of the template books."""
    path = tmp_path / "books.db"
    shutil.copyfile(api_template[0], path)
    with TestClient(create_app(f"sqlite:///{path}")) as client:
        yield client


@pytest.fixture
def api_url(api: TestClient) -> str:
    """The database URL behind ``api``, for checking what it wrote."""
    engine = api.app.state.engine  # type: ignore[attr-defined]
    return str(engine.url)


@pytest.fixture
def auth(api_template: tuple[Path, dict[str, str]]) -> Callable[[str], dict[str, str]]:
    """The request headers that identify an actor of ``API_CAST`` by code."""
    keys = api_template[1]
    return lambda code: {"Authorization": f"Bearer {keys[code]}"}


# --- The MCP interface -------------------------------------------------------------
#
# A server acts as one actor, so a test that needs several actors connects one
# client per actor, all to the same fresh copy of the template books above. Clients
# run on a blocking portal, so tests stay synchronous.


class McpCaller:
    """Calls the MCP tools as one actor, from a synchronous test."""

    def __init__(self, portal: BlockingPortal, client: Client) -> None:
        self._portal = portal
        self._client = client

    def call(self, tool: str, **arguments: Any) -> CallToolResult:
        """The tool's result, whether it was allowed or refused."""
        result: CallToolResult = self._portal.call(
            self._client.call_tool, tool, arguments
        )
        return result

    def __call__(self, tool: str, **arguments: Any) -> Any:
        """The tool's structured result; the test fails if it was refused."""
        result = self.call(tool, **arguments)
        assert not result.is_error, result.content
        return result.structured_content

    def refusal(self, tool: str, **arguments: Any) -> dict[str, Any]:
        """What refused the call; the test fails if it was allowed, or if the
        arguments never reached the tool."""
        result = self.call(tool, **arguments)
        assert result.is_error, result.structured_content
        refusal: dict[str, Any] | None = result.structured_content
        assert refusal is not None, result.content
        return refusal

    def list_tools(self) -> list[Tool]:
        return self._portal.call(self._client.list_tools).tools


class McpServers:
    """MCP servers over one copy of the books, each acting as one actor."""

    def __init__(
        self, url: str, keys: dict[str, str], portal: BlockingPortal, stack: ExitStack
    ) -> None:
        self.url = url
        self._keys = keys
        self._portal = portal
        self._stack = stack
        self._callers: dict[str, McpCaller] = {}

    def __call__(self, code: str) -> McpCaller:
        """A client of the server acting as ``code``, an actor of ``API_CAST``."""
        if code not in self._callers:
            self._callers[code] = self.with_key(self._keys[code])
        return self._callers[code]

    def with_key(self, key: str) -> McpCaller:
        """A client of a server started with ``key``, whatever it identifies."""
        server = create_server(self.url, api_key=key)
        client = self._stack.enter_context(
            self._portal.wrap_async_context_manager(Client(server))
        )
        return McpCaller(self._portal, client)


@pytest.fixture
def mcp_as(
    api_template: tuple[Path, dict[str, str]], tmp_path: Path
) -> Iterator[McpServers]:
    """MCP servers over a fresh copy of the template books: ``mcp_as("agent")``."""
    path = tmp_path / "books.db"
    shutil.copyfile(api_template[0], path)
    with start_blocking_portal() as portal, ExitStack() as stack:
        yield McpServers(f"sqlite:///{path}", api_template[1], portal, stack)

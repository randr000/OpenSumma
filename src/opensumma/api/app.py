"""The FastAPI application: a thin adapter over the workflow and the kernel.

It holds no accounting logic. Reads call the kernel and object layer, which every
read requires READ_ONLY for; every change goes through a workflow operation, which
checks state and permission and records the attempt in the audit log. Callers
identify themselves with an actor's API key.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Response
from sqlalchemy.orm import sessionmaker

from opensumma.api import audit, journal, master_data, objects, reports, schemas
from opensumma.api.errors import install_error_handlers
from opensumma.db import alembic_config, create_engine, get_database_url


def create_app(database_url: str | None = None) -> FastAPI:
    """Build the REST application for the database at ``database_url``.

    The URL defaults to ``OPENSUMMA_DATABASE_URL``. The schema is not created or
    upgraded here; do that with ``alembic upgrade head`` or ``init_db`` first.
    ``/health`` reports whether it is current.
    """
    url = database_url or get_database_url()
    engine = create_engine(url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="OpenSumma",
        version=version("opensumma"),
        summary="A deterministic accounting environment for AI accounting agents.",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.sessions = sessionmaker(engine)
    install_error_handlers(app)
    for router in (
        master_data.router,
        journal.router,
        reports.router,
        objects.router,
        audit.router,
    ):
        app.include_router(router)

    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()

    @app.get("/health", tags=["health"], responses={503: {"model": schemas.Health}})
    def health(response: Response) -> schemas.Health:
        """Whether the database answers and its schema is current. No key needed."""
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        if current != head:
            response.status_code = 503
            return schemas.Health(status="schema_out_of_date")
        return schemas.Health(status="ok")

    return app

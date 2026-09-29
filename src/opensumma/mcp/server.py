"""The MCP server: a thin adapter over the workflow and the kernel.

It holds no accounting logic. The read-only tools read the kernel and the object
layer and need READ_ONLY; the mutating tools are workflow operations, which check
state and permission and record the attempt in the audit log. The two sets are
kept apart in their own modules, and each tool is annotated as read-only or not,
so a host can treat them differently.
"""

import functools
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import sessionmaker

from opensumma.db import create_engine, get_database_url
from opensumma.mcp import mutating_tools, read_tools
from opensumma.mcp.context import ServerState
from opensumma.workflow import audit_json, describe_refusal
from opensumma.workflow.audit import REFUSALS

INSTRUCTIONS = """\
OpenSumma is a double-entry accounting system. You act as one actor: the one \
whose API key started this server. Each tool needs a permission; a tool you lack \
the permission for is refused.

- Amounts are decimal strings such as "120.50", never JSON numbers. Accounts are \
named by code, such as "6100"; periods by code, such as "2026-03".
- Read the chart of accounts, periods, dimensions, and counterparties before \
proposing, and propose only to postable accounts.
- A journal entry moves PROPOSED -> PENDING_APPROVAL -> APPROVED -> POSTED. \
Nobody approves an entry they proposed or submitted. A posted entry is never \
edited; it is reversed.
- validate_journal_entry lists every problem with a stable issue code.
- Give each change a concise reason and evidence references such as \
"vendor_id=42". Both are kept in the audit log; never include private reasoning.
- A refused call returns an error naming what refused it, such as the missing \
permission or the issue codes. Refused attempts are recorded in the audit log too.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
MUTATING = ToolAnnotations(read_only_hint=False, open_world_hint=False)


def create_server(
    database_url: str | None = None, *, api_key: str
) -> MCPServer[ServerState]:
    """Build the MCP server for the database at ``database_url``, acting as the
    actor whose API key is ``api_key``.

    The URL defaults to ``OPENSUMMA_DATABASE_URL``. The schema is not created or
    upgraded here; do that with ``alembic upgrade head`` or ``init_db`` first. The
    key is checked on every call, not here.
    """
    url = database_url or get_database_url()

    @asynccontextmanager
    async def lifespan(server: MCPServer[ServerState]) -> AsyncIterator[ServerState]:
        engine = create_engine(url)
        try:
            yield ServerState(sessions=sessionmaker(engine), api_key=api_key)
        finally:
            engine.dispose()

    tools = [_tool(fn, READ_ONLY) for fn in read_tools.TOOLS]
    tools += [_tool(fn, MUTATING) for fn in mutating_tools.TOOLS]
    return MCPServer(
        "opensumma",
        title="OpenSumma",
        description="A deterministic accounting environment for AI accounting agents.",
        instructions=INSTRUCTIONS,
        version=version("opensumma"),
        tools=tools,
        lifespan=lifespan,
        log_level="WARNING",
    )


def _tool(fn: Callable[..., Any], annotations: ToolAnnotations) -> Tool:
    tool = Tool.from_function(_answering_refusals(fn), annotations=annotations)
    _refuse_unknown_arguments(tool)
    return tool


def _answering_refusals(fn: Callable[..., Any]) -> Callable[..., Any]:
    """``fn``, answering a refusal with the JSON the audit log records for it.

    The refusal is returned as an error result whose structured content names the
    error, its message, and what it names: issue codes, the missing permission, the
    states an action is allowed from. An agent reads the same vocabulary as an
    auditor, and as a caller of the REST interface. Any other failure is left to
    the SDK, which reports it without its details.
    """

    @functools.wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except REFUSALS as error:
            refusal = audit_json(describe_refusal(error))
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(refusal))],
                structured_content=refusal,
                is_error=True,
            )

    return call


def _refuse_unknown_arguments(tool: Tool) -> None:
    """Make ``tool`` refuse arguments it does not declare, and say so in its schema.

    The SDK ignores them by default, so an argument an agent invented, such as
    ``approved``, would silently do nothing. The REST interface refuses unknown
    fields for the same reason.
    """
    declared = tool.fn_metadata.arg_model
    strict = cast(
        type[BaseModel],
        type(
            declared.__name__,
            (declared,),
            {
                "__module__": declared.__module__,
                "model_config": ConfigDict(extra="forbid"),
            },
        ),
    )
    tool.fn_metadata.arg_model = strict  # type: ignore[assignment]
    tool.parameters = strict.model_json_schema(by_alias=True)

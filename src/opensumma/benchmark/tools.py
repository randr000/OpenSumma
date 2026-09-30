"""The tools an agent uses in the benchmark, and the record of every call it makes.

An agent works through the MCP interface's semantic tools, exactly as an agent
connected to ``python -m opensumma.mcp`` would: the same schemas, permissions,
refusals, and audit. The benchmark serves them in-process, and ``Tools`` presents
them synchronously, so an agent needs no asynchronous code and no transport.

Every call is recorded, in order, as its trajectory: the tool, the arguments, and
what came back. Nothing in a trajectory depends on when the run happened, so a
deterministic agent leaves the same trajectory every time.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from anyio.from_thread import BlockingPortal
from mcp import Client
from mcp.types import CallToolResult

REJECTED = "ToolCallRejected"


@dataclass(frozen=True)
class ToolSpec:
    """A tool as an agent sees it: what it does and the arguments it takes."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool


@dataclass(frozen=True)
class ToolCall:
    """One call and its outcome.

    ``result`` is the tool's structured result when it succeeded. ``error`` is what
    refused it: the JSON the audit log records for a refusal, or, for a call no tool
    accepted (an unknown tool, or arguments its schema refuses), the error
    ``ToolCallRejected`` and the reason.
    """

    step: int
    tool: str
    arguments: dict[str, Any]
    result: Any = None
    error: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def rejected(self) -> bool:
        """The call never reached a tool: no tool of that name, or bad arguments."""
        return self.error is not None and self.error["error"] == REJECTED

    @property
    def refused(self) -> bool:
        """A tool ran and a rule refused the call."""
        return self.error is not None and not self.rejected

    @property
    def names_unknown_record(self) -> bool:
        """A tool refused the call for naming a record that does not exist."""
        return self.refused and str((self.error or {})["error"]).startswith("Unknown")

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class Tools:
    """The semantic tools, for one agent on one task, recording every call."""

    _portal: BlockingPortal
    _client: Client
    calls: list[ToolCall] = field(default_factory=list)
    _specs: list[ToolSpec] | None = None

    @property
    def specs(self) -> list[ToolSpec]:
        """Every tool: its name, description, argument schema, and whether it only
        reads."""
        if self._specs is None:
            listed = self._portal.call(self._client.list_tools).tools
            self._specs = [
                ToolSpec(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema),
                    read_only=bool(
                        tool.annotations and tool.annotations.read_only_hint
                    ),
                )
                for tool in listed
            ]
        return self._specs

    def call(self, tool: str, /, **arguments: Any) -> ToolCall:
        """Call ``tool`` with ``arguments`` and record the call.

        Arguments must be JSON values, as an agent connected over MCP would send;
        amounts are strings.
        """
        recorded = _json(arguments)
        outcome: CallToolResult = self._portal.call(
            self._client.call_tool, tool, recorded
        )
        structured = outcome.structured_content
        if not outcome.is_error:
            call = ToolCall(len(self.calls) + 1, tool, recorded, result=structured)
        elif structured is not None and "error" in structured:
            call = ToolCall(len(self.calls) + 1, tool, recorded, error=structured)
        else:
            message = " ".join(
                getattr(block, "text", "") for block in outcome.content
            ).strip()
            call = ToolCall(
                len(self.calls) + 1,
                tool,
                recorded,
                error={"error": REJECTED, "message": message},
            )
        self.calls.append(call)
        return call


def _json(value: dict[str, Any]) -> dict[str, Any]:
    """``value`` as the JSON an MCP client sends, refusing what JSON cannot hold."""
    try:
        decoded: dict[str, Any] = json.loads(json.dumps(value))
    except TypeError as error:
        raise TypeError(f"tool arguments must be JSON values: {error}") from error
    return decoded

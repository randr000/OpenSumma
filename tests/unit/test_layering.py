"""Each layer depends only on the layers beneath it.

This is what makes "financial reports derive from the ledger, never from Accounting
Objects" a property of the code: no kernel module can import the object layer, so
no report can read an object. Likewise the kernel and the object layer never
depend on the workflow engine, which wraps them, and no domain layer depends on the
interfaces or on the frameworks they are built with. The REST and MCP interfaces
are peers: neither imports the other or the other's framework, and what they share
depends on neither. The dataset generator is trusted code beside the workflow: it
uses the domain layers, none of them uses it, and it needs no interface. The
benchmark sits on top: it runs agents through the MCP tools on generated datasets,
and nothing but the command line and the example agents uses it. The example agents
sit above it and see only what it hands an agent, the prompt and the tools.
"""

import ast
from pathlib import Path

import pytest

import opensumma

PACKAGE = Path(opensumma.__file__).parent

# The interfaces, and the frameworks each is built with.
INTERFACES = ("opensumma.interface", "opensumma.api", "opensumma.mcp")
REST = ("opensumma.api", "fastapi", "starlette", "uvicorn")
MCP = ("opensumma.mcp", "mcp")
FRAMEWORKS = ("fastapi", "starlette", "uvicorn", "pydantic", "mcp")

# Each layer, and what it must never import: the layers above it, and the
# frameworks the interfaces are built with.
DATASETS = (
    "opensumma.datasets",
    "opensumma.benchmark",
    "opensumma.agents",
    "opensumma.cli",
)
LAYERS = {
    "kernel": (
        "opensumma.objects",
        "opensumma.workflow",
        *DATASETS,
        *INTERFACES,
        *FRAMEWORKS,
    ),
    "objects": ("opensumma.workflow", *DATASETS, *INTERFACES, *FRAMEWORKS),
    "workflow": (*DATASETS, *INTERFACES, *FRAMEWORKS),
    "datasets": (
        "opensumma.benchmark",
        "opensumma.agents",
        "opensumma.cli",
        *INTERFACES,
        *FRAMEWORKS,
    ),
    # The benchmark serves agents the MCP tools; it has no use for REST. It names
    # the example agents, which are built on it, but never imports them.
    "benchmark": ("opensumma.agents", "opensumma.cli", *REST),
    "interface": (*DATASETS, *REST, *MCP),
    "api": (*DATASETS, *MCP),
    "mcp": (*DATASETS, *REST),
}

# All the example agents may import: what the benchmark hands an agent, and the
# parts of the standard library that compute. Nothing that reaches a database, a
# file, a process, or the network, so the tools are their only way to the books.
AGENT_API = frozenset({"TaskPrompt", "ToolCall", "Tools"})
AGENT_STANDARD_LIBRARY = frozenset(
    {
        "collections",
        "collections.abc",
        "dataclasses",
        "datetime",
        "decimal",
        "re",
        "typing",
    }
)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


@pytest.mark.parametrize(("layer", "above"), LAYERS.items())
def test_a_layer_does_not_import_the_layers_above_it(
    layer: str, above: tuple[str, ...]
) -> None:
    modules = sorted((PACKAGE / layer).glob("*.py"))
    assert modules, f"no modules found in {layer}"
    offending = {
        f"{path.name}: {name}"
        for path in modules
        for name in _imports(path)
        if name.startswith(above)
    }
    assert not offending


def test_the_example_agents_reach_the_books_only_through_the_tools() -> None:
    modules = sorted((PACKAGE / "agents").glob("*.py"))
    assert modules
    offending = set()
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            names: set[tuple[str, str | None]]
            if isinstance(node, ast.Import):
                names = {(alias.name, None) for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "", alias.name) for alias in node.names}
            else:
                continue
            for module, name in names:
                allowed = (
                    module in AGENT_STANDARD_LIBRARY
                    or module.startswith("opensumma.agents")
                    or (module == "opensumma.benchmark" and name in AGENT_API)
                )
                if not allowed:
                    offending.add(f"{path.name}: {module} {name or ''}".strip())
    assert not offending

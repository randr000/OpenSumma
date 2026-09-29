"""Each layer depends only on the layers beneath it.

This is what makes "financial reports derive from the ledger, never from Accounting
Objects" a property of the code: no kernel module can import the object layer, so
no report can read an object. Likewise the kernel and the object layer never
depend on the workflow engine, which wraps them.
"""

import ast
from pathlib import Path

import pytest

import opensumma

PACKAGE = Path(opensumma.__file__).parent

# Each layer, and the layers above it that it must never import.
LAYERS = {
    "kernel": ("opensumma.objects", "opensumma.workflow"),
    "objects": ("opensumma.workflow",),
}


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

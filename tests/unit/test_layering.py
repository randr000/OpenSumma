"""The kernel never depends on the layers above it.

This is what makes "financial reports derive from the ledger, never from Accounting
Objects" a property of the code: no kernel module can import the object layer, so
no report can read an object.
"""

import ast
from pathlib import Path

import opensumma.kernel

KERNEL = Path(opensumma.kernel.__file__).parent
LAYERS_ABOVE = ("opensumma.objects",)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_the_kernel_does_not_import_the_layers_above_it() -> None:
    modules = sorted(KERNEL.glob("*.py"))
    assert modules, "no kernel modules found"
    offending = {
        f"{path.name}: {name}"
        for path in modules
        for name in _imports(path)
        if name.startswith(LAYERS_ABOVE)
    }
    assert not offending

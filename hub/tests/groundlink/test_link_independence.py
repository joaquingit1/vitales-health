"""EI1, D43 and PLAN.md:47: nothing outside the four readers reads the ground link, by import or by DI.

Two seams expose the link: groundlink's ports, and help's GroundLogComposer (whose `link()` catchup provides, with
LinkSnapshot as its value). The scan resolves every import, attribute and string constant in every hub source.
"""

import ast
from pathlib import Path

import pytest

TARGET = "crew_health.groundlink.application.ports"
ALLOWED = {"rules", "catchup", "mission", "groundlink"}
HELP_PORTS = "crew_health.help.application.ports"
DI_SYMBOLS = {"GroundLogComposer", "LinkSnapshot"}
DI_ALLOWED = {"help", "catchup"}


def _references(tree: ast.AST, relative: Path) -> tuple[list[str], set[str]]:
    imports: list[str] = []
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package = ["crew_health", *relative.parts[:-1]]
                module = ".".join(package[: len(package) - node.level + 1] + module.split("."))
            imports.append(module)
            imports.extend(module + "." + alias.name for alias in node.names)
            if module == HELP_PORTS:
                symbols.update(alias.name for alias in node.names if alias.name in DI_SYMBOLS)
        elif isinstance(node, ast.Attribute) and node.attr in DI_SYMBOLS:
            symbols.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(TARGET):
                imports.append(node.value)
            if any(symbol in node.value for symbol in DI_SYMBOLS):
                symbols.add(node.value)
    return imports, symbols


def scan(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    inventory: list[str] = []
    forbidden: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        inventory.append(relative.as_posix())
        imports, symbols = _references(ast.parse(path.read_text(encoding="utf-8")), relative)
        owner = relative.parts[0]
        port_reader = owner not in ALLOWED and any(t == TARGET or t.startswith(TARGET + ".") for t in imports)
        di_reader = owner not in DI_ALLOWED and bool(symbols)
        if port_reader or di_reader:
            forbidden.append(relative.as_posix())
    return tuple(inventory), tuple(forbidden)


@pytest.mark.trace("R48", "EI1", "D43", "E29-02")
def test_no_module_outside_the_readers_reaches_the_link_by_import_type_check_string_or_di(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2] / "src/crew_health"
    inventory, forbidden = scan(root)
    assert inventory and "groundlink/module.py" in inventory
    assert not forbidden
    for name, content in (
        ("direct", f"from {TARGET} import GroundLinkQuery"),
        (
            "type_check",
            f"from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from {TARGET} import GroundLinkQuery",
        ),
        ("dynamic", f'import importlib\nimportlib.import_module("{TARGET}")'),
        ("parent", "from crew_health.groundlink.application import ports"),
        ("relative", "from ..groundlink.application.ports import GroundLinkQuery"),
        (
            "di_reader",
            f"from {HELP_PORTS} import GroundLogComposer\n"
            "def gate(c: GroundLogComposer) -> bool:\n    return c.link().status == 'down'\n",
        ),
        ("di_attribute", f"import {HELP_PORTS} as hp\nkey = hp.GroundLogComposer\n"),
        ("di_value", f"from {HELP_PORTS} import LinkSnapshot\n"),
        ("di_string", 'import importlib\nkey = getattr(importlib.import_module("x"), "GroundLogComposer")\n'),
    ):
        module = tmp_path / name
        module.mkdir()
        (module / "reader.py").write_text(content, encoding="utf-8")
    planted, violations = scan(tmp_path)
    assert len(planted) == 9 and len(violations) == 9

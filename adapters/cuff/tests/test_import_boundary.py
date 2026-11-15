"""Whole-source boundary check, including forbidden hub modules not yet created."""

import ast
from pathlib import Path

import pytest


def boundary_violations(source: str, *, domain: bool) -> list[str]:
    tree = ast.parse(source)
    problems: list[str] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
                if alias.name == "crew_health" or alias.name.startswith("crew_health."):
                    if (
                        not alias.name.startswith("crew_health.contracts_gen.")
                        and alias.name != "crew_health.contracts_gen"
                    ):
                        problems.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                name = node.module + "." + alias.name
                aliases[alias.asname or alias.name] = name
                if name == "crew_health" or name.startswith("crew_health."):
                    if (
                        not name.startswith("crew_health.contracts_gen.")
                        and name != "crew_health.contracts_gen"
                    ):
                        problems.append(name)
    if domain:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = ast.unparse(node.func)
            head, *tail = target.split(".")
            resolved = ".".join([aliases.get(head, head), *tail])
            if resolved in ("datetime.datetime.now", "datetime.now", "time.time", "open", "builtins.open"):
                problems.append(resolved)
    return problems


@pytest.mark.trace("AGENTS 3.9", "E07-03")
def test_all_sources_only_import_generated_hub_contracts_and_domain_has_no_io_or_clock_reads() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    sources = sorted(root.rglob("*.py"))
    assert sources
    for path in sources:
        assert not boundary_violations(
            path.read_text(encoding="utf-8"), domain="domain" in path.parts or path.name == "bpm.py"
        ), str(path)


@pytest.mark.trace("AGENTS 3.9", "E07-03")
@pytest.mark.parametrize(
    "source",
    [
        "import crew_health.future",
        "from crew_health import future",
        "from crew_health.future import Thing",
        "from datetime import datetime as D\nD.now()",
        "import time as t\nt.time()",
        "open('file')",
    ],
)
def test_boundary_guard_rejects_future_modules_alias_clocks_and_domain_io(source: str) -> None:
    assert boundary_violations(source, domain=True)

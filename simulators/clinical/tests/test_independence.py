"""Enforce the adapter prefix ban, which import-linter cannot express as a partial glob."""

import ast
from pathlib import Path

import pytest


@pytest.mark.trace("T-E07-SIM-36", "E07-03")
def test_runtime_imports_are_independent_stdlib_only() -> None:
    import sys

    root = Path(__file__).parents[1] / "src" / "crew_health_sim_clinical"
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                module = name.split(".")[0]
                assert module in sys.stdlib_module_names or module == "crew_health_sim_clinical", (path, name)
                assert not module.startswith("crew_health_adapter_")
                assert module not in {"crew_health", "crew_health_device", "httpx", "fastapi", "bleak"}

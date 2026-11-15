"""T28-28: domain code imports only its domain, kernel and pure stdlib."""

import ast
import sys
from pathlib import Path

import pytest


@pytest.mark.trace("T28-28", "EI1", "PR-I5", "EI6")
def test_domain_imports_and_calls_are_pure() -> None:
    root = Path(__file__).resolve().parents[2] / "src/crew_health/plans/domain"
    paths = sorted(root.rglob("*.py"))
    assert len(paths) >= 10
    forbidden_modules = {
        "os",
        "pathlib",
        "time",
        "random",
        "uuid",
        "subprocess",
        "socket",
        "sqlite3",
        "urllib",
        "http",
        "threading",
        "multiprocessing",
        "importlib",
    }
    forbidden_calls = {"open", "eval", "exec", "print", "input", "__import__"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                )
                for module in modules:
                    assert (
                        module == "crew_health.kernel"
                        or module.startswith("crew_health.kernel.")
                        or module == "crew_health.plans.domain"
                        or module.startswith("crew_health.plans.domain.")
                        or (
                            module.split(".")[0] in sys.stdlib_module_names
                            and module.split(".")[0] not in forbidden_modules
                        )
                    ), (path, module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"now", "utcnow", "today", "load", "dump", "file_digest"}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                assert not any(isinstance(x, ast.Call) for x in ast.walk(node)), path

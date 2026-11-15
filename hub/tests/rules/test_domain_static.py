"""Static rules of the pure domain (AGENTS.md 3, E24 T-E24-032 to 034, 083).

The domain imports only itself, the pure kernel and the standard library, reads no clock, holds no
module level mutable state, never uses `assert`, and fits the function size and complexity rules.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from crew_health.app import import_contracts

pytestmark = [
    pytest.mark.E24,
    pytest.mark.trace("T-E24-032", "T-E24-033", "T-E24-034", "T-E24-083", "INV-E24-10"),
]

PACKAGE_ROOT = Path(import_contracts.__file__).resolve().parents[1]
DOMAIN = PACKAGE_ROOT / "rules" / "domain"
FILES = sorted(DOMAIN.glob("*.py"))
FORBIDDEN_STDLIB = {
    "os",
    "pathlib",
    "time",
    "sqlite3",
    "subprocess",
    "socket",
    "urllib",
    "http",
    "threading",
    "multiprocessing",
    "random",
    "uuid",
    "importlib",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "print"}
CLOCK_CALLS = {"now", "utcnow", "today", "monotonic", "time", "perf_counter"}
MAX_LINES = 60
MAX_COMPLEXITY = 15

EXPECTED_FILES = {
    "__init__", "vocabulary", "definitions", "fingerprint", "warning", "inputs", "views", "decisions", "engine",
    "clear_evidence", "dwell", "occurrences", "escalation", "activation", "time_policy", "visibility", "actions",
    "ordering", "headline", "origin", "hold", "attention", "ownership", "guidance", "insight_bounds", "rendering",
    "errors", "loader_checks",
}  # fmt: skip


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imports(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.append(node.module)
    return found


def test_the_domain_has_exactly_the_files_of_its_ticket() -> None:
    assert {p.stem for p in FILES} == EXPECTED_FILES


def test_the_architecture_gate_accepts_the_rules_domain() -> None:
    import_contracts.check_features(PACKAGE_ROOT, ("rules",))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_imports_are_the_domain_the_pure_kernel_and_the_standard_library(path: Path) -> None:
    for target in imports(parse(path)):
        top = target.split(".")[0]
        if top == "crew_health":
            assert (
                target == "crew_health.rules.domain"
                or target.startswith("crew_health.rules.domain.")
                or target.startswith("crew_health.kernel.")
            ), target
        else:
            assert top in sys.stdlib_module_names and top not in FORBIDDEN_STDLIB, target
        assert (
            "groundlink" not in target and "vehicle" not in target and "adapters" not in target
        )  # T033, T083
        assert not target.startswith(("pydantic", "fastapi", "sqlite3"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_assert_no_dynamic_code_no_wall_clock_no_io(path: Path) -> None:
    for node in ast.walk(parse(path)):
        assert not isinstance(node, ast.Assert), f"assert at line {node.lineno}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in FORBIDDEN_CALLS, f"{func.id} at line {node.lineno}"
            if isinstance(func, ast.Attribute) and func.attr in CLOCK_CALLS:
                assert not (
                    isinstance(func.value, ast.Name) and func.value.id in {"datetime", "date", "time"}
                ), f"clock read at line {node.lineno}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_module_level_mutable_state(path: Path) -> None:
    for node in parse(path).body:
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if value is None:
            continue
        assert not isinstance(
            value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp
        ), f"line {node.lineno}"
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            assert value.func.id not in {"list", "dict", "set", "defaultdict", "deque"}, f"line {node.lineno}"


def functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def complexity(fn: ast.FunctionDef) -> int:
    total = 1
    for node in ast.walk(fn):
        if isinstance(
            node,
            ast.If | ast.For | ast.While | ast.ExceptHandler | ast.IfExp | ast.comprehension | ast.match_case,
        ):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
    return total


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_functions_fit_one_screen_and_stay_under_the_complexity_bound(path: Path) -> None:
    for fn in functions(parse(path)):
        assert fn.end_lineno is not None
        assert fn.end_lineno - fn.lineno + 1 <= MAX_LINES, (
            f"{fn.name} is {fn.end_lineno - fn.lineno + 1} lines"
        )
        assert complexity(fn) <= MAX_COMPLEXITY, f"{fn.name} has complexity {complexity(fn)}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_function_calls_itself_in_the_domain(path: Path) -> None:
    for fn in functions(parse(path)):
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != fn.name, f"{fn.name} recurses"


@pytest.mark.trace("T-E24-032", "INV-E24-10")
def test_no_forecast_input_type_exists_and_only_the_ground_link_input_names_the_link() -> None:
    inputs = (DOMAIN / "inputs.py").read_text(encoding="utf-8").lower()
    assert "forecast" not in inputs
    for path in FILES:
        assert "GroundLinkQuery" not in path.read_text(encoding="utf-8"), (
            path.name
        )  # only an application adapter queries the link

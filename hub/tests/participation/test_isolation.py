"""The domain cannot read a value, a clock or any other module (E22 DE22-2, INV-E22-01, 12, 13; AGENTS section 3)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from crew_health.app.import_contracts import check_features
from crew_health.participation.domain.canonical import result_sha256
from crew_health.participation.domain.derivation import award_views, next_award_events, reached_attributions

from .harness import facts_for, rules

pytestmark = pytest.mark.E22
SOURCE = Path(__file__).resolve().parents[2] / "src" / "crew_health"
DOMAIN = SOURCE / "participation" / "domain"
FILES = sorted(DOMAIN.glob("*.py"))
CLOCK_READS = {
    "now",
    "utcnow",
    "today",
    "monotonic",
    "monotonic_ns",
    "time",
    "perf_counter",
    "uuid4",
    "random",
}


@pytest.mark.trace("T-E22-040", "T-E22-059", "INV-E22-01", "INV-E22-12")
def test_domain_imports_only_itself_the_kernel_and_the_standard_library() -> None:
    check_features(SOURCE, ("participation",))


@pytest.mark.trace("T-E22-040", "INV-E22-01")
def test_no_domain_module_imports_a_sibling_module_of_another_epic() -> None:
    allowed = ("crew_health.participation.domain", "crew_health.kernel")
    for path in FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                assert not name.startswith("crew_health") or name.startswith(allowed), (path.name, name)
                assert name.split(".")[0] not in {
                    "sqlite3",
                    "pydantic",
                    "fastapi",
                    "os",
                    "time",
                    "random",
                    "uuid",
                }


@pytest.mark.trace("AGENTS-3.6", "AGENTS-3.9")
def test_no_wall_clock_randomness_or_module_level_mutable_state_in_the_domain() -> None:
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CLOCK_READS
            ):
                raise AssertionError((path.name, node.func.attr))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
                raise AssertionError((path.name, "module level mutable state"))


@pytest.mark.trace("AGENTS-3.4", "AGENTS-3.5")
def test_functions_fit_on_one_screen_and_runtime_checks_do_not_use_assert() -> None:
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.Assert), path.name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert (node.end_lineno or 0) - node.lineno + 1 <= 60, (path.name, node.name)
            assert not isinstance(node, ast.Global), path.name


@pytest.mark.trace("INV-E22-10", "T-E22-062", "EI10")
def test_the_result_digest_covers_mission_awards_only() -> None:
    r = rules()
    facts = facts_for(range(1, 8))
    events = next_award_events(
        [],
        total=7,
        reached=reached_attributions(facts, 1, 7, r.patch_thresholds),
        rules=r,
        void_applied=False,
    )
    awards = award_views("mara", "demo-mission", "SIM", events)
    assert len(awards) == 1
    assert result_sha256([], awards, None) != result_sha256([], (), None)
    # practice patch designs are configuration read by E31; mission awards come from mission designs only
    practice_ids = {d.patch_design_id for d in r.practice.patch_designs}  # type: ignore[union-attr]
    assert not practice_ids & {a.patch_design_id for a in awards}

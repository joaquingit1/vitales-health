from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-02", "T-E01-08", "SYSTEM.6.5")]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e01_validate_hub", ROOT / "scripts/validate_hub.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source_tree(root: Path) -> tuple[str, str]:
    first = "hub/src/crew_health/platform/domain/rules.py"
    second = "hub/src/crew_health/records/application/append.py"
    for name in (first, second):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def allowed(value: bool) -> bool:\n    if value:\n        return True\n    return False\n"
        )
    empty = root / "hub/src/crew_health/config/domain/__init__.py"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("")
    return first, second


def report(paths: tuple[str, ...], *, covered: int = 2, branches: int = 2) -> dict[str, object]:
    return {
        "meta": {"branch_coverage": True},
        "files": {
            name: {
                "summary": {
                    "covered_lines": 4,
                    "num_statements": 4,
                    "covered_branches": covered,
                    "num_branches": branches,
                },
                "executed_branches": [[1, 2]],
                "missing_branches": [],
            }
            for name in paths
        },
    }


def write_report(root: Path, value: object) -> Path:
    path = root / "coverage.json"
    path.write_text(json.dumps(value))
    return path


def test_coverage_rejects_low_file_hidden_by_aggregate(checker: ModuleType, tmp_path: Path) -> None:
    paths = source_tree(tmp_path)
    value = report(paths)
    value["totals"] = {"percent_covered": 99.99}
    value["files"][paths[0]]["summary"]["covered_branches"] = 1
    empty = "hub/src/crew_health/config/domain/__init__.py"
    value["files"][empty] = {
        "summary": {"covered_lines": 0, "num_statements": 0, "covered_branches": 0, "num_branches": 0},
        "executed_branches": [],
        "missing_branches": [],
    }
    result = checker.coverage_check(tmp_path, write_report(tmp_path, value))
    assert not result.ok
    assert any(paths[0] in error for error in result.errors)
    assert any("EMPTY" in detail and empty in detail for detail in result.details)


def test_coverage_inventory_and_branch_data_are_mandatory(checker: ModuleType, tmp_path: Path) -> None:
    paths = source_tree(tmp_path)
    value = report((paths[0],))
    assert not checker.coverage_check(tmp_path, write_report(tmp_path, value)).ok
    value = report(paths)
    value["meta"]["branch_coverage"] = False
    assert not checker.coverage_check(tmp_path, write_report(tmp_path, value)).ok
    value = report(paths)
    del value["files"][paths[0]]["missing_branches"]
    assert not checker.coverage_check(tmp_path, write_report(tmp_path, value)).ok


def test_coverage_empty_initializers_and_unrelated_results_never_raise_credit(
    checker: ModuleType, tmp_path: Path
) -> None:
    paths = source_tree(tmp_path)
    empty = "hub/src/crew_health/config/domain/__init__.py"
    value = report(paths)
    value["files"][empty] = {
        "summary": {"covered_lines": 0, "num_statements": 0, "covered_branches": 0, "num_branches": 0},
        "executed_branches": [],
        "missing_branches": [],
    }
    result = checker.coverage_check(tmp_path, write_report(tmp_path, value))
    assert result.ok
    value["files"]["hub/src/crew_health/platform/adapters/sqlite.py"] = {
        "summary": {"covered_branches": 0, "num_branches": 999}
    }
    assert checker.coverage_check(tmp_path, write_report(tmp_path, value)) == result
    for name in paths:
        (tmp_path / name).unlink()
    assert not checker.coverage_check(tmp_path, write_report(tmp_path, value)).ok


def test_artifact_inventory_reports_missing_and_tampered_unreferenced_file(
    checker: ModuleType, tmp_path: Path
) -> None:
    import hashlib

    artifact = tmp_path / "unreferenced.json"
    artifact.write_text("{}")
    manifest = {"unreferenced.json": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    assert checker.artifact_check(tmp_path, manifest).ok
    artifact.write_text('{"unexpected":true}')
    assert not checker.artifact_check(tmp_path, manifest).ok
    artifact.unlink()
    assert not checker.artifact_check(tmp_path, manifest).ok


def test_missing_semantic_contributor_never_passes_simulation(checker: ModuleType) -> None:
    from crew_health.config.application.loader import ConfigLoader, LoaderChecks
    from crew_health.kernel.config import CandidateRelease

    result = checker.candidate_check(
        ConfigLoader(LoaderChecks(maximum=20), max_document_bytes=8388608),
        CandidateRelease("release_empty", {}),
        origin="SIM",
    )
    assert not result.ok
    assert any("12.kit" in error for error in result.errors)
    assert any("16.roles" in error for error in result.errors)


def test_mutation_inventory_is_source_bound_and_survivors_fail(checker: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "hub/src/crew_health/records/domain/rules.py"
    path.parent.mkdir(parents=True)
    path.write_text("def allows(value: int) -> bool:\n    return value > 0\n")
    mutants = checker.mutation_inventory(tmp_path)
    assert mutants
    assert all("records/domain/rules.py" in mutant["id"] for mutant in mutants)
    assert not checker.mutations_check(tmp_path, None).ok
    value = {
        "source_hashes": {str(path.relative_to(tmp_path)): mutants[0]["source_sha256"]},
        "mutants": [{"id": mutant["id"], "status": "survived"} for mutant in mutants],
    }
    evidence = tmp_path / "mutants.json"
    evidence.write_text(json.dumps(value))
    assert not checker.mutations_check(tmp_path, evidence).ok


def test_untraced_overlay_edit_rejected_before_effective_validation(
    checker: ModuleType, tmp_path: Path
) -> None:
    assert checker.overlay_check(tmp_path).ok
    overlay = tmp_path / "contracts/decision-overlay"
    overlay.mkdir(parents=True)
    (overlay / "part-a.json").write_text('{"edits":[{"untraced":true}]}')
    assert not checker.overlay_check(tmp_path).ok


def test_safety_evidence_and_complexity_cannot_be_waived(checker: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "hub/src/crew_health/clock/domain/time.py"
    path.parent.mkdir(parents=True)
    path.write_text("def safe(value: bool) -> bool:\n    return value and (value or False)\n")
    inventory = checker.condition_inventory(tmp_path)
    assert inventory
    assert not checker.safety_check(tmp_path, None).ok
    path.write_text("def recursive(value: bool) -> bool:\n    return recursive(value)\n")
    assert any("recursion" in error for error in checker.safety_check(tmp_path, None).errors)
    path.write_text(
        "def complex(value: int) -> int:\n"
        + "    if value:\n        value -= 1\n" * 16
        + "    return value\n"
    )
    assert any("complexity" in error for error in checker.safety_check(tmp_path, None).errors)


def test_gate_runner_runs_every_gate_reports_failures_and_rejects_empty_manifest(tmp_path: Path) -> None:
    import shlex
    import subprocess

    manifest = tmp_path / "commands"
    manifest.write_text("")
    command = ["sh", str(ROOT / "scripts/gates.sh"), str(manifest)]
    assert subprocess.run(command, cwd=ROOT, capture_output=True, timeout=5).returncode == 1
    # Every gate runs even after one fails, and the run still fails: no failure hides the gates after it.
    marker = tmp_path / "later-gate-ran"
    manifest.write_text("false\ntouch " + shlex.quote(str(marker)) + "\n")
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=5, text=True)
    assert failed.returncode == 1
    assert marker.exists()
    assert "Gate summary: 2 commands, 1 failed" in failed.stdout
    manifest.write_text("# Explicit hermetic gate fixture\ntrue\ntrue\n")
    assert subprocess.run(command, cwd=ROOT, capture_output=True, timeout=5).returncode == 0


def test_unreferenced_example_is_still_schema_validated(checker: ModuleType, tmp_path: Path) -> None:
    schemas = tmp_path / "contracts/api/schemas"
    schemas.mkdir(parents=True)
    (schemas / "simple.schema.json").write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"value":{"type":"number"}},"required":["value"],"additionalProperties":false}'
    )
    registry, _, errors = checker._schema_catalog(tmp_path)
    assert not errors
    example = tmp_path / "unreferenced.json"
    example.write_text('{"x-schema":"simple.schema.json","value":{"value":1}}')
    assert checker._validate_example(example, registry) == ()
    example.write_text('{"x-schema":"simple.schema.json","value":{"value":"wrong"}}')
    assert checker._validate_example(example, registry)
    example.write_text('{"x-schema":"missing.schema.json","value":{}}')
    assert checker._validate_example(example, registry)


def test_repository_examples_are_checked_against_the_effective_d12_schemas(checker: ModuleType) -> None:
    registry, schemas, errors = checker._schema_catalog(ROOT)
    assert not errors and "config-unit-mapping.schema.json" in schemas
    for example in ("config/unit-mapping.example.json", "configuration/index.json"):
        assert checker._validate_example(ROOT / "contracts/api/examples" / example, registry) == ()


def test_installed_generated_outputs_reject_unlisted_leaf(checker: ModuleType, tmp_path: Path) -> None:
    import hashlib

    directory = tmp_path / "packages/contracts-ts/src/generated"
    directory.mkdir(parents=True)
    leaf = directory / "api.ts"
    leaf.write_text("export type Value = string;\n")
    manifest = {
        "base": {},
        "effective": {},
        "overlay": None,
        "outputs": {"api.ts": hashlib.sha256(leaf.read_bytes()).hexdigest()},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert checker.installed_outputs_check(tmp_path, "packages/contracts-ts/src/generated", {}).ok
    (directory / "unlisted.ts").write_text("export type Hidden = number;\n")
    assert not checker.installed_outputs_check(tmp_path, "packages/contracts-ts/src/generated", {}).ok

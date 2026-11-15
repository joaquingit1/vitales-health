"""CI must execute the complete collection and measure branches, never combined Cover."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st

pytestmark = pytest.mark.trace("E20", "D31", "DEPLOYMENT.8")
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ci() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_suite", ROOT / "scripts/ci_suite.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_shards_cover_every_test_once_and_isolate_expensive_groups(ci: ModuleType) -> None:
    tests = {
        "hub/tests/a/test_first.py": ["hub/tests/a/test_first.py::test_a"],
        "hub/tests/b/test_second.py": ["hub/tests/b/test_second.py::test_b"],
        "hub/tests/contracts/test_guard.py": ["hub/tests/contracts/test_guard.py::test_guard"],
        "hub/tests/app/test_installed_wheel.py": ["hub/tests/app/test_installed_wheel.py::test_wheel"],
    }
    plan = ci.make_plan(tests, {"hub/tests/a/test_first.py": 40.0})
    ci.validate_plan(plan)
    assert plan["groups"]["contracts"] == ["hub/tests/contracts/test_guard.py"]
    assert plan["groups"]["wheel"] == ["hub/tests/app/test_installed_wheel.py"]
    assert sorted(f for files in plan["groups"].values() for f in files) == sorted(tests)
    assert plan == ci.make_plan(dict(reversed(list(tests.items()))), {}) | {"estimates": plan["estimates"]}


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "empty", "unsafe"])
def test_invalid_collection_manifest_fails_closed(ci: ModuleType, fault: str) -> None:
    name = "hub/tests/test_safe.py"
    plan = ci.make_plan({name: [name + "::test_one"]}, {})
    if fault == "missing":
        plan["groups"]["core-0"].clear()
    elif fault == "duplicate":
        plan["groups"]["wheel"].append(name)
    elif fault == "unknown":
        plan["groups"]["core-0"].append("hub/tests/test_unknown.py")
    elif fault == "empty":
        plan["tests"][name] = []
    else:
        plan["tests"]["../secret.py"] = ["../secret.py::test_one"]
    with pytest.raises(ValueError):
        ci.validate_plan(plan)


def test_cross_platform_collection_cannot_drop_or_add_test(ci: ModuleType) -> None:
    expected = {"hub/tests/test_a.py": ["hub/tests/test_a.py::test_one"]}
    ci.verify_collection(expected, expected)
    for actual in ({}, {"hub/tests/test_a.py": ["hub/tests/test_a.py::test_other"]}):
        with pytest.raises(ValueError, match="collection mismatch"):
            ci.verify_collection(expected, actual)


def test_ratchet_uses_branch_fraction_and_does_not_mask_one_file(ci: ModuleType) -> None:
    old = {"a.py": {"covered": 1, "total": 2}, "b.py": {"covered": 1, "total": 2}}
    now = {"a.py": {"covered": 49, "total": 100}, "b.py": {"covered": 100, "total": 100}}
    failures = ci.ratchet(now, old, {"a.py", "b.py"})
    assert failures == ["a.py: branch coverage regressed 1/2 -> 49/100"]
    assert ci.ratchet({"a.py": {"covered": 2, "total": 4}}, {"a.py": old["a.py"]}, {"a.py"}) == []


def test_new_and_unmeasured_existing_source_are_not_exempt(ci: ModuleType) -> None:
    assert ci.ratchet({"new.py": {"covered": 18, "total": 20}}, {}, {"new.py"})
    assert ci.ratchet({"new.py": {"covered": 19, "total": 20}}, {}, {"new.py"}) == []
    assert ci.ratchet({}, {"a.py": {"covered": 1, "total": 2}}, {"a.py"}) == [
        "a.py: source exists but coverage is missing"
    ]
    assert ci.ratchet({}, {"deleted.py": {"covered": 1, "total": 2}}, set()) == []
    assert ci.ratchet({}, {}, {"new.py"}) == ["new.py: source exists but coverage is missing"]
    assert ci.ratchet({"a.py": {"covered": 0, "total": 0}}, {}, {"a.py"}) == []
    assert ci.ratchet({"a.py": {"covered": 1, "total": 2}}, {"a.py": {"covered": 0, "total": 0}}, {"a.py"})


def test_raw_arc_combine_requires_every_shard_and_current_collection(
    ci: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    name = "hub/src/crew_health/sample/domain/rules.py"
    source = tmp_path / name
    source.parent.mkdir(parents=True)
    source.write_text(
        "def allowed(value):\n    if value:\n        return True\n    return False\n", encoding="utf-8"
    )
    (tmp_path / "hub/pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "add", "hub"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI fixture",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    files = [f"hub/tests/test_{i}.py" for i in range(4)] + [
        "hub/tests/contracts/test_contract.py",
        "hub/tests/app/test_installed_wheel.py",
    ]
    plan = ci.make_plan({f: [f + "::test_one"] for f in files}, {})
    manifest = tmp_path / "plan.json"
    ci.write_json(manifest, plan)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    baseline = tmp_path / ".github/ci/branch-baseline.json"
    ci.write_json(baseline, {"files": {name: {"covered": 2, "total": 2}}})
    for i, group in enumerate(ci.GROUPS):
        folder = tmp_path / "artifacts" / group
        folder.mkdir(parents=True)
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from coverage import Coverage; import runpy,sys; "
                    "c=Coverage(branch=True,data_file=sys.argv[1]); c.start(); "
                    "runpy.run_path(sys.argv[2])['allowed'](sys.argv[3]=='True'); c.stop(); c.save()"
                ),
                str(folder / "coverage.data"),
                str(source),
                str(bool(i % 2)),
            ],
            check=True,
        )
        expected = {f: plan["tests"][f] for f in plan["groups"][group]}
        ci.write_json(folder / "collection.json", {"expected": expected, "actual": expected})
        ci.write_json(folder / "identity.json", {"group": group, "manifest_sha256": digest, "source": sha})
    output = tmp_path / "output"
    output.mkdir()
    assert ci.coverage_report(tmp_path / "artifacts", output, manifest) == 0
    result = json.loads((output / "branch-ratchet.json").read_text(encoding="utf-8"))
    assert (result["covered"], result["total"]) == (2, 2)
    # The same arc from several shards is counted once; an absent shard never produces green.
    missing = tmp_path / "artifacts/wheel/coverage.data"
    missing.unlink()
    with pytest.raises(ValueError, match="missing or duplicated coverage artifact: wheel"):
        ci.coverage_report(tmp_path / "artifacts", output, manifest)


@given(st.integers(min_value=1, max_value=10_000), st.integers(min_value=1, max_value=100))
def test_fraction_comparison_has_no_rounding_escape(total: int, scale: int) -> None:
    spec = importlib.util.spec_from_file_location("ci_suite_property", ROOT / "scripts/ci_suite.py")
    assert spec is not None and spec.loader is not None
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    # Just below the baseline always fails, even when a displayed percentage rounds equally.
    base = {"a.py": {"covered": total, "total": total + 1}}
    now = {"a.py": {"covered": total * scale - 1, "total": (total + 1) * scale}}
    assert ci.ratchet(now, base, {"a.py"})

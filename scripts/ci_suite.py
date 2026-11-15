"""Complete, portable pytest shards and the D31 actual branch coverage ratchet.

No tests are selected by changed paths. A full collection produces the manifest;
each worker proves its exact selected collection before executing any test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import TypedDict

import pytest

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("core-0", "core-1", "core-2", "core-3", "contracts", "wheel")
MAX_TESTS = 100_000
MAX_FILES = 10_000


class Counts(TypedDict):
    covered: int
    total: int


class Plan(TypedDict):
    version: int
    tests: dict[str, list[str]]
    groups: dict[str, list[str]]
    estimates: dict[str, float]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_file(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and name.endswith(".py")
        and name.startswith(("hub/tests/", "eval/scenarios/"))
    )


def make_plan(tests: dict[str, list[str]], timings: dict[str, float]) -> Plan:
    groups: dict[str, list[str]] = {name: [] for name in GROUPS}
    loads = {name: 0.0 for name in GROUPS}
    for name in sorted(tests, key=lambda f: (-timings.get(f, float(len(tests[f]))), f)):
        if name.startswith("hub/tests/contracts/"):
            group = "contracts"
        elif name == "hub/tests/app/test_installed_wheel.py":
            group = "wheel"
        else:
            group = min(GROUPS[:4], key=lambda g: (loads[g], g))
        groups[group].append(name)
        loads[group] += timings.get(name, float(len(tests[name])))
    plan: Plan = {"version": 1, "tests": tests, "groups": groups, "estimates": loads}
    validate_plan(plan)
    return plan


def validate_plan(plan: Plan) -> None:
    tests = plan["tests"]
    groups = plan["groups"]
    ids = [node for nodes in tests.values() for node in nodes]
    selected = [name for files in groups.values() for name in files]
    if (
        plan["version"] != 1
        or set(groups) != set(GROUPS)
        or not 0 < len(tests) <= MAX_FILES
        or not 0 < len(ids) <= MAX_TESTS
        or len(ids) != len(set(ids))
        or Counter(selected) != Counter({name: 1 for name in tests})
        or any(not safe_file(name) or not nodes for name, nodes in tests.items())
        or any(not node.startswith(name + "::") for name, nodes in tests.items() for node in nodes)
    ):
        raise ValueError("invalid or incomplete collection manifest")


def verify_collection(expected: dict[str, list[str]], actual: dict[str, list[str]]) -> None:
    left = Counter(node for nodes in expected.values() for node in nodes)
    right = Counter(node for nodes in actual.values() for node in nodes)
    if left != right or set(expected) != set(actual):
        raise ValueError(
            f"collection mismatch: missing={list((left - right).elements())[:10]} "
            f"extra={list((right - left).elements())[:10]}"
        )


def ratchet(current: dict[str, Counts], baseline: dict[str, Counts], inventory: set[str]) -> list[str]:
    failures: list[str] = []
    for name in sorted(inventory):
        now = current.get(name)
        old = baseline.get(name)
        if now is None:
            failures.append(f"{name}: source exists but coverage is missing")
            continue
        covered, total = now["covered"], now["total"]
        if not 0 <= covered <= total:
            raise ValueError("invalid branch counts")
        if total == 0:
            continue
        if old is None or old["total"] == 0:
            if covered * 100 < total * 95:
                failures.append(f"{name}: new branch coverage {covered}/{total} is below 95%")
        elif covered * old["total"] < old["covered"] * total:
            failures.append(
                f"{name}: branch coverage regressed {old['covered']}/{old['total']} -> {covered}/{total}"
            )
    return failures


def source_inventory(root: Path) -> set[str]:
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z", "hub/src/crew_health"], cwd=root)
        .decode("utf-8")
        .split("\0")
    )
    files = {
        name for name in tracked if name.endswith(".py") and {"domain", "application"} & set(name.split("/"))
    }
    if not files or any(not (root / name).is_file() for name in files):
        raise ValueError("tracked source inventory is missing")
    # A present file cannot evade the gate by being absent from the report or git index.
    present = {
        path.relative_to(root).as_posix()
        for path in (root / "hub/src/crew_health").rglob("*.py")
        if {"domain", "application"} & set(path.relative_to(root).parts)
    }
    if present != files:
        raise ValueError("source inventory differs from tracked files")
    return files


class Collection:
    def __init__(
        self,
        output: Path,
        expected: dict[str, list[str]] | None = None,
        timings: dict[str, float] | None = None,
    ) -> None:
        self.output = output
        self.expected = expected
        self.timings = timings or {}

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        tests: dict[str, list[str]] = {}
        for item in session.items:
            name = item.path.resolve().relative_to(ROOT).as_posix()
            suffix = item.nodeid.split("::", 1)[1]
            tests.setdefault(name, []).append(name + "::" + suffix)
        if self.expected is not None:
            write_json(self.output, {"expected": self.expected, "actual": tests})
            try:
                verify_collection(self.expected, tests)
            except ValueError as exc:
                raise pytest.UsageError(str(exc)) from exc
        else:
            write_json(self.output, make_plan(tests, self.timings))


def coverage_report(data_root: Path, output: Path, manifest: Path) -> int:
    from coverage import Coverage

    plan: Plan = json.loads(manifest.read_text(encoding="utf-8"))
    validate_plan(plan)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    inputs: list[str] = []
    for group in GROUPS:
        paths = list(data_root.rglob(f"{group}/coverage.data"))
        if len(paths) != 1:
            raise ValueError(f"missing or duplicated coverage artifact: {group}")
        proof = json.loads((paths[0].parent / "collection.json").read_text(encoding="utf-8"))
        expected = {name: plan["tests"][name] for name in plan["groups"][group]}
        verify_collection(expected, proof["expected"])
        verify_collection(expected, proof["actual"])
        identity = json.loads((paths[0].parent / "identity.json").read_text(encoding="utf-8"))
        source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if identity != {"group": group, "manifest_sha256": digest, "source": source}:
            raise ValueError("coverage evidence belongs to another source or collection")
        inputs.append(str(paths[0]))
    cov = Coverage(config_file=str(ROOT / "hub/pyproject.toml"), data_file=str(output / "combined.data"))
    cov.combine(data_paths=inputs, strict=True, keep=True)
    cov.save()
    report_path = output / "coverage.json"
    inventory = source_inventory(ROOT)
    # Tests import temporary fixture modules whose source only exists on their worker.
    # The policy measures the complete tracked domain/application inventory, not those fixtures.
    cov.json_report(outfile=str(report_path), include=[str(ROOT / name) for name in sorted(inventory)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    current: dict[str, Counts] = {}
    for filename, info in report["files"].items():
        path = Path(filename)
        name = (
            path.resolve().relative_to(ROOT).as_posix()
            if path.is_absolute() and path.is_relative_to(ROOT)
            else path.as_posix()
        )
        current[name] = {
            "covered": info["summary"]["covered_branches"],
            "total": info["summary"]["num_branches"],
        }
    baseline = json.loads((ROOT / ".github/ci/branch-baseline.json").read_text(encoding="utf-8"))
    if any((ROOT / name).exists() for name in set(baseline["files"]) - inventory):
        raise ValueError("baseline source exists outside measured inventory")
    failures = ratchet(current, baseline["files"], inventory)
    covered = sum(current[name]["covered"] for name in inventory if name in current)
    total = sum(current[name]["total"] for name in inventory if name in current)
    print(f"Actual domain/application branches: {covered}/{total}; target95%, per-file D31 ratchet")
    for failure in failures:
        print(f"::error::{failure}")
    write_json(output / "branch-ratchet.json", {"covered": covered, "total": total, "failures": failures})
    return int(bool(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["collect", "run", "coverage"])
    parser.add_argument("--manifest", type=Path, default=Path("evidence/plan.json"))
    parser.add_argument("--group", choices=GROUPS)
    parser.add_argument("--evidence", type=Path, default=Path("evidence"))
    parser.add_argument("--data-root", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    if args.mode == "coverage":
        return coverage_report(args.data_root, args.evidence, args.manifest)
    if args.mode == "collect":
        timings = json.loads((ROOT / ".github/ci/test-timings.json").read_text(encoding="utf-8"))["files"]
        return int(
            pytest.main(
                ["-c", "hub/pyproject.toml", "--collect-only", "-q", "hub/tests", "eval/scenarios"],
                plugins=[Collection(args.manifest, timings=timings)],
            )
        )
    if args.group is None:
        parser.error("run requires --group")
    plan: Plan = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate_plan(plan)
    files = plan["groups"][args.group]
    if not files:
        raise ValueError("empty shard refuses accidental whole-suite execution")
    expected = {name: plan["tests"][name] for name in files}
    os.environ["COVERAGE_FILE"] = str(args.evidence / "coverage.data")
    write_json(
        args.evidence / "identity.json",
        {
            "group": args.group,
            "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        },
    )
    return int(
        pytest.main(
            [
                "-c",
                "hub/pyproject.toml",
                "-vv",
                "--durations=30",
                "--cov",
                "--cov-branch",
                "--cov-config=hub/pyproject.toml",
                "--cov-report=term",
                f"--junitxml={args.evidence / 'junit.xml'}",
                *files,
            ],
            plugins=[Collection(args.evidence / "collection.json", expected=expected)],
        )
    )


if __name__ == "__main__":
    sys.exit(main())

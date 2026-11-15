"""T-E01-01 SYSTEM 6.5 guarded base inventory and deterministic generation."""

import operator
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from crew_health.contracts_gen.build import object_map, verify_base

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM-6.5")]
ROOT = Path(__file__).resolve().parents[3]


def test_base_inventory() -> None:
    assert verify_base(ROOT)


def test_javascript_runtime_is_declared_and_locked() -> None:
    import json

    package = object_map(json.loads((ROOT / "package.json").read_bytes()))
    runtime = object_map(object_map(package["devEngines"])["runtime"])
    assert runtime["name"] == "node"
    assert runtime["onFail"] == "download"
    version = runtime["version"]
    assert isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version)
    documents: list[object] = list(yaml.safe_load_all((ROOT / "pnpm-lock.yaml").read_bytes()))
    project = object_map(documents[-1])
    importer = object_map(object_map(project["importers"])["."])
    node = object_map(object_map(importer["devDependencies"])["node"])
    assert node == {"specifier": "runtime:" + version, "version": "runtime:" + version}
    package_lock = object_map(object_map(project["packages"])["node@runtime:" + version])
    variants = object_map(package_lock["resolution"])["variants"]
    assert isinstance(variants, list) and variants
    for variant in variants:
        resolution = object_map(object_map(variant)["resolution"])
        assert re.fullmatch(r"sha256-[A-Za-z0-9+/]+={0,2}", str(resolution["integrity"]))


def _pnpm_launcher() -> list[str]:
    """Return how to start pnpm without relying on the PATH node.

    pnpm installed through npm, corepack or Homebrew is a node script (``#!/usr/bin/env node``). The test below puts a
    broken node first on PATH to prove project tools run on the pinned runtime; that must not also break the launcher
    itself, so a script launcher is started with the interpreter found before PATH is changed.
    """
    pnpm = shutil.which("pnpm")
    assert pnpm is not None, "pnpm is required"
    with open(pnpm, "rb") as launcher:
        first_line = launcher.readline()
    if first_line.startswith(b"#!") and b"node" in first_line:
        node = shutil.which("node")
        assert node is not None, "a script pnpm launcher needs node to start"
        return [node, os.path.realpath(pnpm)]
    return [pnpm]


@pytest.mark.parametrize("command", [["typecheck"], ["exec", "openapi-typescript", "--version"]])
def test_javascript_tools_use_project_runtime(tmp_path: Path, command: list[str]) -> None:
    launcher = _pnpm_launcher()
    broken_node = tmp_path / ("node.cmd" if os.name == "nt" else "node")
    broken_node.write_text("@exit /b 97\n" if os.name == "nt" else "#!/bin/sh\nexit 97\n", encoding="utf-8")
    broken_node.chmod(0o755)
    result = subprocess.run(
        [*launcher, "-C", "packages/contracts-ts", *command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "npm_config_offline": "true",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("fault", ["changed", "missing", "extra", "manifest"])
def test_base_corruption_fails(tmp_path: Path, fault: str) -> None:
    shutil.copytree(ROOT / "contracts/api", tmp_path / "contracts/api")
    shutil.copytree(ROOT / "contracts/reference", tmp_path / "contracts/reference")
    shutil.copy(ROOT / "contracts/SHA256SUMS", tmp_path / "contracts/SHA256SUMS")
    shutil.copytree(ROOT / "docs/product", tmp_path / "docs/product")
    target = tmp_path / "contracts/api/openapi.yaml"
    if fault == "changed":
        target.write_bytes(b"corrupt")
    elif fault == "missing":
        target.unlink()
    elif fault == "extra":
        (target.parent / "extra.json").write_text("{}", encoding="utf-8")
    else:
        (tmp_path / "docs/product/package-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_base(tmp_path)


def test_repeated_python_generation_is_byte_identical(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import build

    first, second = tmp_path / "first", tmp_path / "second"
    build(ROOT, output=first)
    build(ROOT, output=second)
    left = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    right = {p.relative_to(second): p.read_bytes() for p in second.rglob("*") if p.is_file()}
    assert left == right
    import json

    from crew_health.contracts_gen.build import verify_overlay

    manifest = json.loads((first / "manifest.json").read_bytes())
    overlay = verify_overlay(ROOT, verify_base(ROOT))
    assert overlay is not None
    # D12 and accepted D22 supersede only the explicitly listed base schema values.
    assert manifest["base"] == verify_base(ROOT)
    assert manifest["overlay"] == overlay.record()
    assert manifest["effective"] == overlay.effective(manifest["base"])
    assert set(manifest["effective"]) - set(manifest["base"]) == {
        "api/schemas/config-unit-mapping.schema.json"
    }
    assert {key for key in manifest["base"] if manifest["effective"][key] != manifest["base"][key]} == {
        "api/schemas/common.schema.json",
        "api/schemas/config-index.schema.json",
        "api/schemas/config-kit-map.schema.json",
        "api/schemas/config-onboard-plans.schema.json",
        "api/schemas/kit-check.schema.json",
        "api/schemas/kit-map-view.schema.json",
        "api/schemas/kit-map-what-if.schema.json",
        "api/schemas/model-action.schema.json",
        "api/schemas/plan-view.schema.json",
        "api/schemas/requests.schema.json",
    }
    assert "schemas/config_unit_mapping_schema.py" in manifest["outputs"]
    assert set(manifest["outputs"]) == {p.as_posix() for p in left if p.as_posix() != "manifest.json"}


def test_generated_python_imports_are_sorted(tmp_path: Path) -> None:
    import sys

    from crew_health.contracts_gen.build import build

    output = tmp_path / "generated"
    build(ROOT, output=output)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "I",
            "--config",
            str(ROOT / "hub/pyproject.toml"),
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_unallocated_overlay_and_unlisted_output_fail(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import publish

    (tmp_path / "destination").mkdir()
    (tmp_path / "staging").mkdir()
    (tmp_path / "destination/unlisted.py").write_text("unowned", encoding="utf-8")
    with pytest.raises(ValueError):
        publish(tmp_path / "destination", tmp_path / "staging", {}, modules={}, source_hashes={})
    assert (tmp_path / "destination/unlisted.py").read_text(encoding="utf-8") == "unowned"


def test_generated_enums_and_closed_error_model() -> None:
    import json

    from pydantic import ValidationError

    from crew_health.contracts_gen.enums import (
        ERROR_STATUS,
        ConfigName,
        ErrorCode,
        EventType,
        EvidenceKind,
        Role,
    )
    from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope

    source = json.loads((ROOT / "contracts/api/schemas/common.schema.json").read_bytes())["$defs"]
    # D12 adds the accepted effective member while the guarded base enum stays at 18.
    assert set(ConfigName) == set(source["config_name"]["enum"]) | {"unit-mapping"}
    assert set(Role) == set(source["role"]["enum"])
    assert set(EvidenceKind) == set(source["evidence_ref"]["properties"]["kind"]["enum"])
    assert len(ErrorCode) == len(ERROR_STATUS)
    assert len(EventType) == 36
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate(
            {
                "code": "NOT_FOUND",
                "message": "missing",
                "retryable": False,
                "field_errors": [],
                "correlation_id": "correlation",
                "current_state": None,
                "supported_alternative": None,
                "extra": "forbidden",
            }
        )


def test_overlay_cannot_enter_without_the_shared_binding(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import build

    (tmp_path / "contracts").mkdir()
    for name in ["api", "reference"]:
        shutil.copytree(ROOT / "contracts" / name, tmp_path / "contracts" / name)
    shutil.copy(ROOT / "contracts/SHA256SUMS", tmp_path / "contracts/SHA256SUMS")
    (tmp_path / "docs").mkdir()
    shutil.copytree(ROOT / "docs/product", tmp_path / "docs/product")
    (tmp_path / "contracts/decision-overlay").mkdir()
    with pytest.raises(ValueError, match="manifest binding"):
        build(tmp_path)


def _overlay_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "contracts").mkdir(parents=True)
    (root / "docs/requests").mkdir(parents=True)
    shutil.copy(ROOT / "contracts/SHA256SUMS", root / "contracts/SHA256SUMS")
    shutil.copytree(ROOT / "contracts/decision-overlay", root / "contracts/decision-overlay")
    shutil.copytree(ROOT / "contracts/api/schemas", root / "contracts/api/schemas")
    shutil.copy(
        ROOT / "docs/requests/COORDINATOR-DECISIONS.md", root / "docs/requests/COORDINATOR-DECISIONS.md"
    )
    return root


def _rehash_overlay(root: Path) -> None:
    import hashlib
    import json

    directory = root / "contracts/decision-overlay"
    manifest = object_map(json.loads((directory / "manifest.json").read_bytes()))
    manifest["files"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.iterdir())
        if p.is_file() and p.name != "manifest.json"
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_verified_overlay_traces_every_edit_to_an_accepted_a3_row(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import accepted_traces, verify_overlay

    base = verify_base(ROOT)
    overlay = verify_overlay(_overlay_root(tmp_path), base)
    assert overlay is not None
    decisions, rows = accepted_traces(ROOT)
    assert overlay.traces
    for trace in overlay.traces:
        assert trace["decision"] in decisions
        lines = trace["a3"]
        assert isinstance(lines, list) and lines
        assert all(trace["decision"] in rows[line] for line in lines)
    assert set(overlay.effective(base)) - set(base) == set(overlay.additions)


@pytest.mark.parametrize(
    "fault", ["untraced", "pending", "tampered", "unlisted", "unbound", "rebased", "operation"]
)
def test_overlay_fault_is_rejected_before_effective_generation(tmp_path: Path, fault: str) -> None:
    import json

    from crew_health.contracts_gen.build import verify_overlay

    root = _overlay_root(tmp_path)
    directory = root / "contracts/decision-overlay"
    edits = object_map(json.loads((directory / "part-a.json").read_bytes()))
    items = edits["edits"]
    assert isinstance(items, list) and items
    edit = object_map(items[0])
    if fault == "untraced":
        edit["a3"] = ["CONTRACTS.md:1"]
    elif fault == "pending":
        edit["decision"] = "D999"
    elif fault == "operation":
        edit["op"] = "replace_file"
    if fault in {"untraced", "pending", "operation"}:
        edits["edits"] = [edit]
        (directory / "part-a.json").write_text(json.dumps(edits), encoding="utf-8")
        _rehash_overlay(root)
    elif fault == "tampered":
        schema = directory / "config-unit-mapping.schema.json"
        schema.write_bytes(schema.read_bytes().replace(b'"const": 4', b'"const": 3'))
    elif fault == "unlisted":
        (directory / "manifest.sig").write_bytes(b"unverified")
    elif fault == "unbound":
        (directory / "extra.schema.json").write_text("{}", encoding="utf-8")
        _rehash_overlay(root)
    else:
        (root / "contracts/SHA256SUMS").write_bytes(b"")
    with pytest.raises(ValueError):
        verify_overlay(root, verify_base(ROOT))


def test_duplicate_inventory_and_missing_manifest_fail(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import load_json

    (tmp_path / "duplicate.json").write_text('{"files":[],"files":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_json(tmp_path / "duplicate.json")
    with pytest.raises(FileNotFoundError):
        load_json(tmp_path / "missing.json")


def test_generated_status_tables_are_immutable() -> None:
    from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode

    with pytest.raises(TypeError):
        apply_operator("setitem", ERROR_STATUS, ErrorCode.INTERNAL_ERROR, 200)


def test_repeated_typescript_generation_is_byte_identical(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import build

    first, second = tmp_path / "first", tmp_path / "second"
    build(ROOT, typescript=True, output=first)
    build(ROOT, typescript=True, output=second)
    left = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    right = {p.relative_to(second): p.read_bytes() for p in second.rglob("*") if p.is_file()}
    assert left == right


def test_generated_leaves_are_not_hidden_by_repository_ignores() -> None:
    import json

    manifest = json.loads((ROOT / "hub/src/crew_health/contracts_gen/manifest.json").read_bytes())
    ignored_directories = {
        line[:-1]
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.endswith("/") and "/" not in line[:-1]
    }
    components = {part for name in manifest["outputs"] for part in Path(name).parts[:-1]}
    assert not components & ignored_directories


def test_generated_instant_preserves_the_wire_pattern() -> None:
    from pydantic import ValidationError

    from crew_health.contracts_gen.schemas.common_schema import Instant

    offset = Instant.model_validate("2026-01-01T00:00:00Z").root.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    for value in ["2026-01-01T00:00:00+01:00", "2026-01-01T00:00:00+00:00", 0]:
        with pytest.raises(ValidationError):
            Instant.model_validate(value)


def test_enum_status_source_must_match_package_inventory(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import enum_source

    (tmp_path / "contracts").mkdir()
    shutil.copytree(ROOT / "contracts/api", tmp_path / "contracts/api")
    (tmp_path / "docs/product").mkdir(parents=True)
    shutil.copy(ROOT / "docs/product/package-manifest.json", tmp_path / "docs/product/package-manifest.json")
    source = (ROOT / "docs/product/CONTRACTS.md").read_text(encoding="utf-8")
    (tmp_path / "docs/product/CONTRACTS.md").write_text(
        source.replace("| `INTERNAL_ERROR` | 500 |", "| `INTERNAL_ERROR` | 200 |"), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        enum_source(tmp_path, tmp_path / "contracts/api/schemas")


def test_enum_inventory_copy_works_with_windows_legacy_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.write_text

    def legacy_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        return original(path, data, encoding=encoding or "cp1252", errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", legacy_write)
    test_enum_status_source_must_match_package_inventory(tmp_path)


def apply_operator(name: str, target: object, key: object, value: object) -> None:
    operation: Callable[[object, object, object], None] = getattr(operator, name)
    operation(target, key, value)

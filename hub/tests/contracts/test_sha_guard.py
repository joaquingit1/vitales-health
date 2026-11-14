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


@pytest.mark.parametrize("command", [["typecheck"], ["exec", "openapi-typescript", "--version"]])
def test_javascript_tools_use_project_runtime(tmp_path: Path, command: list[str]) -> None:
    broken_node = tmp_path / ("node.cmd" if os.name == "nt" else "node")
    broken_node.write_text("@exit /b 97\n" if os.name == "nt" else "#!/bin/sh\nexit 97\n")
    broken_node.chmod(0o755)
    result = subprocess.run(
        ["pnpm", "-C", "packages/contracts-ts", *command],
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
        (target.parent / "extra.json").write_text("{}")
    else:
        (tmp_path / "docs/product/package-manifest.json").write_text("{}")
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

    manifest = json.loads((first / "manifest.json").read_bytes())
    assert manifest["overlay"] is None
    assert manifest["effective"] == manifest["base"]
    assert set(manifest["outputs"]) == {str(p) for p in left if str(p) != "manifest.json"}


def test_unallocated_overlay_and_unlisted_output_fail(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import publish

    (tmp_path / "destination").mkdir()
    (tmp_path / "staging").mkdir()
    (tmp_path / "destination/unlisted.py").write_text("unowned")
    with pytest.raises(ValueError):
        publish(tmp_path / "destination", tmp_path / "staging", {}, modules={}, source_hashes={})
    assert (tmp_path / "destination/unlisted.py").read_text() == "unowned"


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
    assert set(ConfigName) == set(source["config_name"]["enum"])
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
        (tmp_path / "contracts" / name).symlink_to(ROOT / "contracts" / name, target_is_directory=True)
    shutil.copy(ROOT / "contracts/SHA256SUMS", tmp_path / "contracts/SHA256SUMS")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/product").symlink_to(ROOT / "docs/product", target_is_directory=True)
    (tmp_path / "contracts/decision-overlay").mkdir()
    with pytest.raises(ValueError, match="manifest binding"):
        build(tmp_path)


def test_duplicate_inventory_and_missing_manifest_fail(tmp_path: Path) -> None:
    from crew_health.contracts_gen.build import load_json

    (tmp_path / "duplicate.json").write_text('{"files":[],"files":[]}')
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
        for line in (ROOT / ".gitignore").read_text().splitlines()
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
    (tmp_path / "contracts/api").symlink_to(ROOT / "contracts/api", target_is_directory=True)
    (tmp_path / "docs/product").mkdir(parents=True)
    shutil.copy(ROOT / "docs/product/package-manifest.json", tmp_path / "docs/product/package-manifest.json")
    source = (ROOT / "docs/product/CONTRACTS.md").read_text()
    (tmp_path / "docs/product/CONTRACTS.md").write_text(
        source.replace("| `INTERNAL_ERROR` | 500 |", "| `INTERNAL_ERROR` | 200 |")
    )
    with pytest.raises(ValueError):
        enum_source(tmp_path)


def apply_operator(name: str, target: object, key: object, value: object) -> None:
    operation: Callable[[object, object, object], None] = getattr(operator, name)
    operation(target, key, value)

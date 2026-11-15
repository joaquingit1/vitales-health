from __future__ import annotations

import argparse
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import Quantity
from crew_health.knowledge.adapters.policy_file import load_policy
from crew_health.knowledge.adapters.registry_file import (
    build_registry_input,
    byte_limit,
    input_copies,
    load_manifest,
    parse_json,
    pinned_file,
    registry_snapshot,
)
from crew_health.knowledge.application.registry_service import RegistryService
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.module import setting_specs
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SystemIds


def tooling_settings() -> HubSettings:
    """Offline authoring tools use registered ceilings, never deployment defaults."""
    specs = setting_specs()
    values: dict[str, object] = {}
    for spec in specs:
        if spec.maximum is None:
            raise EvidenceInvalid("setting_maximum")
        values[spec.key] = int(spec.maximum) if spec.unit is None else Quantity(spec.maximum, spec.unit)
    return ValidatedSettings(specs, values)


def read_input(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise EvidenceInvalid("input_bound")
    return raw


def sync_inputs(root: Path, settings: HubSettings) -> None:
    maximum = byte_limit(settings, "knowledge.bundle.max_member_bytes")
    raw = read_input(
        root / "knowledge/inputs/manifest.json", byte_limit(settings, "knowledge.bundle.max_manifest_bytes")
    )
    manifest = load_manifest(raw, settings)
    verified: list[tuple[Path, bytes]] = []
    total = 0
    for member in manifest.files:
        content = pinned_file(root, member.source, member.sha256, maximum)
        total += len(content)
        if total > byte_limit(settings, "knowledge.bundle.max_total_expanded_bytes"):
            raise EvidenceInvalid("input_bound")
        target = root / member.destination
        if target.resolve() != target or target.parent.resolve() != target.parent:
            raise EvidenceInvalid("input_path")
        verified.append((target, content))
    for target, content in verified:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.write_bytes(content) != len(content):
            raise EvidenceInvalid("input_write")


def build_registry(root: Path, settings: HubSettings) -> bytes:
    maximum = byte_limit(settings, "knowledge.bundle.max_manifest_bytes")
    manifest = read_input(root / "knowledge/inputs/manifest.json", maximum)
    inventories = read_input(root / "knowledge/inputs/inventories.json", maximum)
    policy = load_policy(
        read_input(root / "knowledge/policy/release-policy.json", maximum), settings=settings
    )
    value = build_registry_input(
        input_copies(root, manifest, settings), sha256(manifest).hexdigest(), inventories, settings
    )
    parsed = parse_json(
        inventories, max_bytes=maximum, max_depth=settings.integer("knowledge.bundle.max_json_depth")
    )
    if not isinstance(parsed, dict) or not isinstance(parsed.get("planning_sentinel"), str):
        raise EvidenceInvalid("inventories")
    sentinel = parsed["planning_sentinel"]
    if not isinstance(sentinel, str):
        raise EvidenceInvalid("inventories")
    RegistryService(
        registry_snapshot(value, sentinel),
        ids=SystemIds(),
        max_rows=settings.integer("knowledge.registry.max_rows"),
        max_source_ids=settings.integer("knowledge.query.max_source_ids"),
        max_url_chars=byte_limit(settings, "knowledge.bundle.max_line_bytes"),
        tracking_parameters=policy.tracking_parameters,
    )
    return canonical_json(value.model_dump())


def validate(root: Path, settings: HubSettings) -> tuple[str, ...]:
    failures = []
    maximum = byte_limit(settings, "knowledge.bundle.max_member_bytes")
    for name in (
        "inputs-manifest",
        "inventories",
        "stage-map",
        "release-policy",
        "registry",
        "review-export",
        "evidence-content",
        "release-manifest",
    ):
        path = root / "knowledge/schemas" / (name + ".schema.json")
        if not path.is_file():
            failures.append("missing_schema:" + name)
            continue
        value = parse_json(
            read_input(path, maximum),
            max_bytes=maximum,
            max_depth=settings.integer("knowledge.bundle.max_json_depth"),
        )
        if not isinstance(value, dict):
            raise EvidenceInvalid("schema")
        Draft202012Validator.check_schema(value)
    try:
        build_registry(root, settings)
    except EvidenceInvalid as error:
        failures.append(str(error))
    return tuple(failures)


def main(command: str = "validate", argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        settings = tooling_settings()
        if command == "sync_inputs":
            sync_inputs(root, settings)
            failures: tuple[str, ...] = ()
        elif command == "build_registry":
            raw = build_registry(root, settings)
            target = root / "knowledge/registry/registry.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.write_bytes(raw) != len(raw):
                raise EvidenceInvalid("registry_write")
            failures = ()
        else:
            failures = validate(root, settings)
        print(canonical_json({"command": command, "findings": list(failures)}).decode("utf-8"))
        return 1 if failures else 0
    except EvidenceInvalid, SchemaError:
        print(canonical_json({"code": ErrorCode.CONFIG_INVALID.value}).decode("utf-8"))
    except ProblemError as error:
        print(canonical_json({"code": error.code.value}).decode("utf-8"))
    except OSError:
        print(canonical_json({"code": ErrorCode.INTERNAL_ERROR.value}).decode("utf-8"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

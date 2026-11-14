"""SYSTEM 6.5 verified base generation; D19 keeps effective equal to base."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


def object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("expected JSON object")
    return {str(k): v for k, v in value.items()}


def load_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    return object_map(value)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def package_source(root: Path, name: str) -> bytes:
    package = root / "docs/product"
    members = load_json(package / "package-manifest.json").get("files")
    if not isinstance(members, list):
        raise ValueError("missing package inventory")
    records = [object_map(member) for member in members]
    matching = [record for record in records if record.get("path") == name]
    raw = (package / name).read_bytes()
    if len(matching) != 1 or matching[0].get("sha256") != digest(raw) or matching[0].get("bytes") != len(raw):
        raise ValueError("package source hash mismatch: " + name)
    return raw


def source_files(package: Path) -> tuple[str, ...]:
    paths = ["api/openapi.yaml", "input-dictionary.json", "assessment-families.json"]
    for directory in ("api/schemas", "api/examples"):
        paths.extend(
            p.relative_to(package).as_posix() for p in (package / directory).rglob("*") if p.is_file()
        )
    return tuple(sorted(paths))


def verify_base(root: Path) -> dict[str, str]:
    contracts = root / "contracts"
    listed: dict[str, str] = {}
    for line in (contracts / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ((?:api|reference)/[^\s]+)", line)
        if match is None or match[2] in listed or ".." in Path(match[2]).parts:
            raise ValueError("invalid SHA inventory")
        listed[match[2]] = match[1]
    actual = {
        p.relative_to(contracts).as_posix()
        for d in ("api", "reference")
        for p in (contracts / d).rglob("*")
        if p.is_file()
    }
    if actual != listed.keys():
        raise ValueError("base inventory differs")
    for name, sha in listed.items():
        if digest((contracts / name).read_bytes()) != sha:
            raise ValueError("base hash mismatch: " + name)
    package = root / "docs/product"
    if package.is_dir():
        manifest = load_json(package / "package-manifest.json")
        members = manifest.get("files")
        if not isinstance(members, list):
            raise ValueError("missing package inventory")
        entries = [object_map(member) for member in members]
        source = {str(e["path"]): e for e in entries}
        if len(source) != len(entries):
            raise ValueError("duplicate package inventory")
        expected = {p if p.startswith("api/") else "reference/" + p: p for p in source_files(package)}
        if expected.keys() != listed.keys():
            raise ValueError("package inventory differs")
        manifest_api = {
            p for p in source if p == "api/openapi.yaml" or p.startswith(("api/schemas/", "api/examples/"))
        }
        if manifest_api != {p for p in expected.values() if p.startswith("api/")}:
            raise ValueError("package API inventory differs")
        for name, original in expected.items():
            raw = (package / original).read_bytes()
            if original not in source or source[original].get("sha256") != digest(raw):
                raise ValueError("package hash mismatch: " + original)
            if source[original].get("bytes") != len(raw) or digest(raw) != listed[name]:
                raise ValueError("package copy mismatch: " + original)
    return listed


def string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected string inventory")
    return tuple(str(v) for v in value)


def enum_source(root: Path) -> bytes:
    schemas = root / "contracts/api/schemas"
    common = object_map(load_json(schemas / "common.schema.json")["$defs"])
    error = object_map(load_json(schemas / "error.schema.json")["properties"])
    event = object_map(load_json(schemas / "event.schema.json")["properties"])
    evidence = object_map(object_map(common["evidence_ref"])["properties"])
    groups = {
        "ErrorCode": object_map(error["code"])["enum"],
        "EventType": object_map(event["type"])["enum"],
        "ConfigName": object_map(common["config_name"])["enum"],
        "Role": object_map(common["role"])["enum"],
        "EvidenceKind": object_map(evidence["kind"])["enum"],
    }
    lines = [
        "# Generated from verified package schemas.\n",
        "from enum import StrEnum\n",
        "from collections.abc import Mapping, Iterator\n",
        "from dataclasses import dataclass\n",
        "\n@dataclass(frozen=True, slots=True)\nclass _Table[K, V](Mapping[K, V]):\n"
        "    pairs: tuple[tuple[K, V], ...]\n"
        "    def __getitem__(self, key: K) -> V:\n"
        "        for candidate, value in self.pairs:\n"
        "            if candidate == key:\n                return value\n"
        "        raise KeyError(key)\n"
        "    def __iter__(self) -> Iterator[K]:\n"
        "        return (key for key, _ in self.pairs)\n"
        "    def __len__(self) -> int:\n        return len(self.pairs)\n",
    ]
    for name, values in groups.items():
        lines.append(f"\nclass {name}(StrEnum):\n")
        members = string_list(values)
        keys = [re.sub(r"[^A-Za-z0-9_]", "_", v).upper() for v in members]
        if len(set(keys)) != len(keys):
            raise ValueError("enum identifier collision")
        for key, value in zip(keys, members, strict=True):
            lines.append(f"    {key} = {value!r}\n")
    table = dict(
        re.findall(
            r"^\| `([A-Z_]+)` \| (\d{3}) \|", package_source(root, "CONTRACTS.md").decode("utf-8"), re.M
        )
    )
    codes = string_list(groups["ErrorCode"])
    if set(table) != set(codes):
        raise ValueError("error status table differs from schema")
    retry_text = str(object_map(error["retryable"])["description"])
    retry = set(re.findall(r"[A-Z]+(?:_[A-Z]+)+", retry_text))
    if not retry <= set(codes):
        raise ValueError("unknown retryability code")
    for name, values in (
        ("ERROR_STATUS", {c: int(table[c]) for c in codes}),
        ("ERROR_RETRYABLE", {c: c in retry for c in codes}),
    ):
        kind = "int" if name == "ERROR_STATUS" else "bool"
        lines.append(f"\n{name}: Mapping[ErrorCode, {kind}] = _Table((\n")
        lines.extend(f"    (ErrorCode.{c}, {v!r}),\n" for c, v in values.items())
        lines.append("))\n")
    return "".join(lines).encode()


def run(command: list[str], *, cwd: Path) -> None:
    # E01 11 registry.startup_timeout_ms ceiling, converted to seconds.
    subprocess.run(command, cwd=cwd, check=True, timeout=300)


def python_outputs(root: Path, destination: Path) -> None:
    run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(root / "contracts/api/schemas"),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(destination / "schemas"),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.14",
            "--disable-timestamp",
            "--use-standard-collections",
            "--field-constraints",
            "--strict-nullable",
            "--use-union-operator",
            "--use-title-as-name",
            "--enum-field-as-literal",
            "all",
            "--no-allow-remote-refs",
            "--formatters",
            "builtin",
        ],
        cwd=root,
    )
    preserve_instant_pattern(root, destination / "schemas/common_schema.py")
    (destination / "enums.py").write_bytes(enum_source(root))


def preserve_instant_pattern(root: Path, generated: Path) -> None:
    common = object_map(load_json(root / "contracts/api/schemas/common.schema.json")["$defs"])
    pattern = object_map(common["instant"])["pattern"]
    if not isinstance(pattern, str):
        raise ValueError("instant pattern is missing")
    tree = ast.parse(generated.read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Instant"]
    if len(classes) != 1:
        raise ValueError("generated Instant model is missing or ambiguous")
    validator = ast.parse(
        "@_schema_field_validator('root', mode='before')\n"
        "@classmethod\n"
        "def _validate_wire_pattern(cls, value: object) -> object:\n"
        f"    if not isinstance(value, str) or _schema_pattern_search({pattern!r}, value) is None:\n"
        "        raise ValueError('wire instant pattern mismatch')\n"
        "    return value\n"
    ).body[0]
    classes[0].body.append(validator)
    imports = ast.parse(
        "from re import search as _schema_pattern_search\n"
        "from pydantic import field_validator as _schema_field_validator\n"
    ).body
    position = 0
    while position < len(tree.body) and isinstance(tree.body[position], ast.ImportFrom):
        node = tree.body[position]
        if not isinstance(node, ast.ImportFrom) or node.module != "__future__":
            break
        position += 1
    tree.body[position:position] = imports
    generated.write_text(
        "# Generated from common.schema.json; preserves #/$defs/instant/pattern.\n" + ast.unparse(tree) + "\n"
    )


def typescript_outputs(root: Path, destination: Path) -> None:
    run(
        [
            "pnpm",
            "exec",
            "openapi-typescript",
            str(root / "contracts/api/openapi.yaml"),
            "--output",
            str(destination / "api.ts"),
        ],
        cwd=root / "packages/contracts-ts",
    )
    (destination / "client.ts").write_text(
        "// Generated typed transport from the guarded paths inventory.\n"
        'import createClient from "openapi-fetch";\n'
        'import type { paths } from "./api.js";\n'
        'export type * from "./api.js";\n'
        "export const client = createClient<paths>;\n"
    )


def publish(
    destination: Path,
    staging: Path,
    base: Mapping[str, str],
    *,
    modules: Mapping[str, str],
    source_hashes: Mapping[str, str],
) -> None:
    outputs = {
        p.relative_to(staging).as_posix(): digest(p.read_bytes())
        for p in sorted(staging.rglob("*"))
        if p.is_file()
    }
    manifest = destination / "manifest.json"
    owned: set[str] = set()
    if manifest.exists():
        owned = set(object_map(load_json(manifest)["outputs"]))
    existing = {
        p.relative_to(destination).as_posix()
        for p in destination.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    handwritten = {"__init__.py", "build.py", "manifest.json"}
    if existing - handwritten - owned:
        raise ValueError("unlisted generated leaves: " + str(sorted(existing - handwritten - owned)))
    if owned - outputs.keys():
        raise ValueError("obsolete outputs require explicit allocation review")
    destination.mkdir(parents=True, exist_ok=True)
    for name in outputs:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((staging / name).read_bytes())
    data: dict[str, object] = {
        "sources": dict(source_hashes),
        "base": dict(base),
        "effective": dict(base),
        "overlay": None,
        "modules": dict(modules),
        "outputs": outputs,
    }
    if "schemas/common_schema.py" in outputs:
        binding = "api/schemas/common.schema.json#/$defs/instant/pattern"
        data["constraint_bindings"] = {binding: "schemas/common_schema.py:Instant._validate_wire_pattern"}
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def build(root: Path, *, typescript: bool = False, output: Path | None = None) -> None:
    root = root.resolve()
    base = verify_base(root)
    if (root / "contracts/decision-overlay").exists():
        raise ValueError("D12 overlay integration requires the allocated manifest binding")
    target = root / (
        "packages/contracts-ts/src/generated" if typescript else "hub/src/crew_health/contracts_gen"
    )
    with tempfile.TemporaryDirectory(prefix="crew-health-generation-") as directory:
        staging = Path(directory)
        if typescript:
            typescript_outputs(root, staging)
        else:
            python_outputs(root, staging)
        from crew_health.app.import_contracts import discover

        modules = discover(root / "hub/src/crew_health", max_modules=128)
        source_hashes = {"docs/product/CONTRACTS.md": digest(package_source(root, "CONTRACTS.md"))}
        publish(
            output if output is not None else target,
            staging,
            base,
            modules=modules,
            source_hashes=source_hashes,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--typescript", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    build(args.root, typescript=args.typescript, output=args.output)


if __name__ == "__main__":
    main()

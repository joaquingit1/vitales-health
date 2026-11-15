"""SYSTEM 6.5 generation from the verified base plus the traced D12 decision overlay."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

OVERLAY_DIRECTORY = "contracts/decision-overlay"
OVERLAY_EDITS = "part-a.json"
DECISIONS = "docs/requests/COORDINATOR-DECISIONS.md"
# Parsing bound for the overlay edit list; the overlay holds accepted A3 edits only.
MAX_OVERLAY_EDITS = 1024


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
    for line in (contracts / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
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


@dataclass(frozen=True, slots=True)
class Overlay:
    """A verified D12 decision overlay: hashed files and edits traced to accepted A3 rows."""

    version: str
    manifest_sha256: str
    files: Mapping[str, str]
    additions: Mapping[str, str]
    traces: tuple[Mapping[str, object], ...]
    replacements: Mapping[str, bytes]

    def effective(self, base: Mapping[str, str]) -> dict[str, str]:
        result = dict(base)
        for target, source in sorted(self.additions.items()):
            result[target] = self.files[source]
        result.update((target, digest(raw)) for target, raw in self.replacements.items())
        return dict(sorted(result.items()))

    def record(self) -> dict[str, object]:
        return {
            "version": self.version,
            "manifest_sha256": self.manifest_sha256,
            "files": dict(sorted(self.files.items())),
            "traces": [dict(trace) for trace in self.traces],
        }


def accepted_traces(root: Path) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """Accepted Part A decision IDs and the A3 superseded-line table (line -> decisions)."""
    text = (root / DECISIONS).read_text(encoding="utf-8")
    part_a = text.split("\n# Part B.", 1)[0]
    head, separator, table = part_a.partition("\n## A3.")
    if not separator:
        raise ValueError("A3 superseded-line table is missing")
    decisions = frozenset(re.findall(r"(?m)^\| ([DQ]\d+) \|", head))
    rows: dict[str, frozenset[str]] = {}
    for line, column in re.findall(r"(?m)^\| `([^`]+)` \| ([^|]+) \|", table):
        cited = frozenset(part.strip() for part in column.split(","))
        rows[line] = rows.get(line, frozenset()) | cited
    if not decisions or not rows:
        raise ValueError("accepted decision tables are empty")
    return decisions, rows


def _overlay_inventory(root: Path, directory: Path) -> tuple[str, str, dict[str, str]]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("D12 overlay requires the manifest binding: manifest.json is missing")
    manifest = load_json(manifest_path)
    if set(manifest) != {"version", "base_inventory_sha256", "files"}:
        raise ValueError("overlay manifest fields differ from the binding")
    version, bound = manifest["version"], manifest["base_inventory_sha256"]
    if not isinstance(version, str) or not version:
        raise ValueError("overlay version is missing")
    if bound != digest((root / "contracts/SHA256SUMS").read_bytes()):
        raise ValueError("overlay is bound to a different base inventory")
    files = object_map(manifest["files"])
    if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) for sha in files.values()):
        raise ValueError("overlay file hashes are invalid")
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} - {
        "manifest.json"
    }
    if actual != files.keys() or OVERLAY_EDITS not in files:
        raise ValueError("overlay inventory differs from its manifest")
    for name, sha in sorted(files.items()):
        if digest((directory / name).read_bytes()) != sha:
            raise ValueError("overlay hash mismatch: " + name)
    hashes = {name: str(sha) for name, sha in files.items()}
    return version, digest(manifest_path.read_bytes()), hashes


def _traced_edit(
    edit: object, decisions: frozenset[str], rows: Mapping[str, frozenset[str]]
) -> dict[str, object]:
    record = object_map(edit)
    fields = {"id", "decision", "a3", "op", "target"}
    if record.get("op") == "add_file":
        fields.add("source")
    elif record.get("op") == "replace":
        fields.update({"pointer", "expected", "value"})
    else:
        raise ValueError("unsupported overlay operation")
    if set(record) != fields or not isinstance(record["id"], str) or not record["id"]:
        raise ValueError("unsupported overlay edit fields")
    decision, lines = record["decision"], string_list(record["a3"])
    if not isinstance(decision, str) or decision not in decisions:
        raise ValueError("overlay edit cites no accepted Part A decision: " + str(record["id"]))
    if not lines or any(decision not in rows.get(line, frozenset()) for line in lines):
        raise ValueError("untraced overlay edit: " + str(record["id"]))
    return record


def verify_overlay(root: Path, base: Mapping[str, str]) -> Overlay | None:
    """None keeps D19's base-only interim; any present overlay must verify completely."""
    directory = root / OVERLAY_DIRECTORY
    if not directory.exists():
        return None
    version, manifest_sha256, files = _overlay_inventory(root, directory)
    document = load_json(directory / OVERLAY_EDITS)
    if set(document) != {"version", "edits"} or document["version"] != version:
        raise ValueError("overlay edits version differs from its manifest")
    edits = document["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_OVERLAY_EDITS:
        raise ValueError("overlay edit list is empty or exceeds its bound")
    decisions, rows = accepted_traces(root)
    additions: dict[str, str] = {}
    traces: list[Mapping[str, object]] = []
    replacements: dict[str, bytes] = {}
    identifiers: set[object] = set()
    for edit in edits:
        record = _traced_edit(edit, decisions, rows)
        target = record["target"]
        if (
            not isinstance(target, str)
            or not re.fullmatch(r"api/schemas/[a-z0-9-]+\.schema\.json", target)
            or record["id"] in identifiers
        ):
            raise ValueError("overlay edit target invalid: " + str(record["id"]))
        identifiers.add(record["id"])
        if record["op"] == "add_file":
            source = record["source"]
            if (
                target in base
                or target in additions
                or not isinstance(source, str)
                or source not in files
                or source == OVERLAY_EDITS
            ):
                raise ValueError("overlay edit source invalid: " + str(record["id"]))
            additions[target] = source
        else:
            if target not in base:
                raise ValueError("overlay pointer target absent from base")
            raw = replacements.get(target)
            if raw is None:
                raw = (root / "contracts" / target).read_bytes()
                if digest(raw) != base[target]:
                    raise ValueError("overlay pointer base hash mismatch")
            replacements[target] = replace_pointer(raw, record)
        traces.append(record)
    if set(additions.values()) != files.keys() - {OVERLAY_EDITS}:
        raise ValueError("overlay file is not bound to a traced edit")
    return Overlay(version, manifest_sha256, files, additions, tuple(traces), replacements)


def _pointer_child(parent: object, part: str) -> object:
    if isinstance(parent, dict) and part in parent:
        child: object = parent[part]
        return child
    if (
        isinstance(parent, list)
        and re.fullmatch(r"0|[1-9][0-9]*", part) is not None
        and len(part) <= len(str(len(parent)))
    ):
        index = int(part)
        if index < len(parent):
            child = parent[index]
            return child
    raise ValueError("overlay JSON pointer does not resolve")


def replace_pointer(raw: bytes, edit: Mapping[str, object]) -> bytes:
    """Replace one existing JSON value, rejecting stale expectations and invalid pointers."""
    pointer = edit["pointer"]
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer.count("/") > 64:
        raise ValueError("invalid overlay JSON pointer")
    parts = pointer[1:].split("/")
    if any(re.search(r"~(?![01])", part) for part in parts):
        raise ValueError("invalid overlay JSON pointer escape")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in parts]
    document: object = json.loads(raw, object_pairs_hook=unique_object)
    parent = document
    for part in parts[:-1]:
        parent = _pointer_child(parent, part)
    current = _pointer_child(parent, parts[-1])
    if current != edit["expected"]:
        raise ValueError("overlay JSON pointer expectation differs")
    if isinstance(parent, dict):
        parent[parts[-1]] = edit["value"]
    elif isinstance(parent, list):
        parent[int(parts[-1])] = edit["value"]
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def effective_schemas(directory: Path, paths: tuple[Path, ...]) -> dict[str, bytes]:
    """The runtime schema set by file name: the guarded base schemas in `directory` with the verified decision
    overlay applied (D12) when `directory` is the repository's contracts/api/schemas. Any other directory, such
    as a test fixture tree, is used exactly as given."""
    schemas = {path.name: path.read_bytes() for path in paths}
    resolved = directory.resolve()
    root = resolved.parents[2]
    if resolved != (root / "contracts/api/schemas").resolve():
        return schemas
    overlay = verify_overlay(root, verify_base(root))
    if overlay is not None:
        for target, source in overlay.additions.items():
            schemas[Path(target).name] = (root / OVERLAY_DIRECTORY / source).read_bytes()
        for target, raw in overlay.replacements.items():
            schemas[Path(target).name] = raw
    return schemas


def effective_tree(
    root: Path, overlay: Overlay | None, effective: Mapping[str, str], destination: Path
) -> Path:
    """Materialize the effective API tree and re-verify every byte against the effective inventory."""
    api = destination / "api"
    shutil.copytree(root / "contracts/api", api)
    if overlay is not None:
        for target, source in sorted(overlay.additions.items()):
            (destination / target).write_bytes((root / OVERLAY_DIRECTORY / source).read_bytes())
        for target, raw in sorted(overlay.replacements.items()):
            (destination / target).write_bytes(raw)
    actual = {
        p.relative_to(destination).as_posix(): digest(p.read_bytes()) for p in api.rglob("*") if p.is_file()
    }
    if actual != {name: sha for name, sha in effective.items() if name.startswith("api/")}:
        raise ValueError("effective contract tree differs from its verified inventory")
    return api


def string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected string inventory")
    return tuple(str(v) for v in value)


def enum_source(root: Path, schemas: Path) -> bytes:
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
    # Resolve through PATH and PATHEXT: on Windows pnpm is a .cmd shim that CreateProcess never finds by bare name.
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError(f"{command[0]} not found on PATH")
    # E01 11 registry.startup_timeout_ms ceiling, converted to seconds.
    subprocess.run([executable, *command[1:]], cwd=cwd, check=True, timeout=300)


def python_outputs(root: Path, api: Path, destination: Path) -> None:
    schemas = api / "schemas"
    run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(schemas),
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
    preserve_instant_pattern(schemas, destination / "schemas/common_schema.py")
    for generated in sorted((destination / "schemas").glob("*.py")):
        flatten_overridden_bases(generated)
    (destination / "enums.py").write_bytes(enum_source(root, schemas))
    run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "I",
            "--fix",
            "--config",
            str(root / "hub/pyproject.toml"),
            str(destination),
        ],
        cwd=root,
    )
    run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--config",
            str(root / "hub/pyproject.toml"),
            str(destination),
        ],
        cwd=root,
    )


def preserve_instant_pattern(schemas: Path, generated: Path) -> None:
    common = object_map(load_json(schemas / "common.schema.json")["$defs"])
    pattern = object_map(common["instant"])["pattern"]
    if not isinstance(pattern, str):
        raise ValueError("instant pattern is missing")
    tree = ast.parse(generated.read_text(encoding="utf-8"))
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
        "# Generated from common.schema.json; preserves #/$defs/instant/pattern.\n"
        + ast.unparse(tree)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _fields(node: ast.ClassDef) -> tuple[set[str], ast.expr | None, bool]:
    """A generated model's annotated field names, its model_config value and whether the body holds only those."""
    names: set[str] = set()
    config: ast.expr | None = None
    plain = True
    for statement in node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
        elif isinstance(statement, ast.Assign) and [ast.unparse(target) for target in statement.targets] == [
            "model_config"
        ]:
            config = statement.value
        elif not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)):
            plain = False
    return names, config, plain


def flatten_overridden_bases(generated: Path) -> None:
    """datamodel-codegen models an allOf over a oneOf as a class inheriting each oneOf branch, then redeclares
    every field in the combined class. Where a branch pins a field to null, the redeclaration widens the base
    field, which mypy --strict rejects. When the combined class redeclares every field of its in-module bases
    and the bases carry nothing else (no validators, the same model_config or none), the bases add no runtime
    behavior, so the class is rebased on BaseModel; pydantic validation is unchanged."""
    text = generated.read_text(encoding="utf-8")
    tree = ast.parse(text)
    models = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    lines = text.splitlines(keepends=True)
    for node in models.values():
        bases = [base.id for base in node.bases if isinstance(base, ast.Name)]
        if len(bases) < 2 or len(bases) != len(node.bases) or not all(name in models for name in bases):
            continue
        fields, config, plain = _fields(node)
        inherited = [_fields(models[name]) for name in bases]
        if not plain or not all(
            base_plain
            and base_fields <= fields
            and (base_config is None or (config is not None and ast.dump(base_config) == ast.dump(config)))
            for base_fields, base_config, base_plain in inherited
        ):
            continue
        header = lines[node.lineno - 1]
        if not header.startswith("class " + node.name + "(") or not header.rstrip().endswith("):"):
            raise ValueError("generated class header spans lines: " + node.name)
        lines[node.lineno - 1] = "class " + node.name + "(BaseModel):\n"
    generated.write_text("".join(lines), encoding="utf-8", newline="\n")


def typescript_outputs(root: Path, api: Path, destination: Path) -> None:
    run(
        [
            "pnpm",
            "exec",
            "openapi-typescript",
            str(api / "openapi.yaml"),
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
        "export const client = createClient<paths>;\n",
        encoding="utf-8",
        newline="\n",
    )


def publish(
    destination: Path,
    staging: Path,
    base: Mapping[str, str],
    *,
    modules: Mapping[str, str],
    source_hashes: Mapping[str, str],
    effective: Mapping[str, str] | None = None,
    overlay: Mapping[str, object] | None = None,
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
        "effective": dict(base if effective is None else effective),
        "overlay": None if overlay is None else dict(overlay),
        "modules": dict(modules),
        "outputs": outputs,
    }
    if "schemas/common_schema.py" in outputs:
        binding = "api/schemas/common.schema.json#/$defs/instant/pattern"
        data["constraint_bindings"] = {binding: "schemas/common_schema.py:Instant._validate_wire_pattern"}
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def build(root: Path, *, typescript: bool = False, output: Path | None = None) -> None:
    root = root.resolve()
    base = verify_base(root)
    overlay = verify_overlay(root, base)
    effective = dict(base) if overlay is None else overlay.effective(base)
    target = root / (
        "packages/contracts-ts/src/generated" if typescript else "hub/src/crew_health/contracts_gen"
    )
    with (
        tempfile.TemporaryDirectory(prefix="crew-health-generation-") as directory,
        tempfile.TemporaryDirectory(prefix="crew-health-effective-") as contract,
    ):
        # datamodel-codegen's builtin formatter reads line length, string normalization and first party names
        # from the nearest pyproject.toml above its output, so a TMPDIR inside any Python project changed the
        # generated bytes. An empty pyproject.toml beside the staging tree pins those settings to the defaults.
        (Path(directory) / "pyproject.toml").write_bytes(b"")
        staging = Path(directory) / "generated"
        staging.mkdir()
        api = effective_tree(root, overlay, effective, Path(contract))
        if typescript:
            typescript_outputs(root, api, staging)
        else:
            python_outputs(root, api, staging)
        from crew_health.app.import_contracts import discover

        modules = discover(root / "hub/src/crew_health", max_modules=128)
        source_hashes = {"docs/product/CONTRACTS.md": digest(package_source(root, "CONTRACTS.md"))}
        publish(
            output if output is not None else target,
            staging,
            base,
            modules=modules,
            source_hashes=source_hashes,
            effective=effective,
            overlay=None if overlay is None else overlay.record(),
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

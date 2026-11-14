"""E01 acceptance checks. Missing evidence is a failure, never a passed placeholder."""

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from crew_health.config.application.loader import ConfigLoader, LoaderChecks
from crew_health.config.domain.checks import parse_document, walk
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.errors import ProblemError


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    errors: tuple[str, ...]
    details: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return cast(dict[str, object], value)


def json_document(path: Path) -> dict[str, object]:
    return object_map(parse_document(path.read_bytes()))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_file(root: Path, name: str) -> Path:
    path = root / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("artifact path escapes inventory root")
    return path


def owned_paths(root: Path, layers: tuple[tuple[str, str], ...]) -> tuple[Path, ...]:
    paths = tuple(
        sorted(
            {
                path
                for module, layer in layers
                for path in (root / "hub/src/crew_health" / module / layer).rglob("*.py")
            }
        )
    )
    if len(paths) > 10000:
        raise ValueError("source inventory exceeds E01 checker bound")
    return paths


def coverage_layers() -> tuple[tuple[str, str], ...]:
    return tuple(
        (module, layer)
        for module in ("platform", "config", "clock", "events", "records")
        for layer in ("domain", "application")
    )


def executable(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    return any(
        not isinstance(node, ast.Pass)
        and not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        for node in tree.body
    )


def integer(summary: Mapping[str, object], key: str) -> int:
    value = summary.get(key)
    if type(value) is not int or value < 0:
        raise ValueError("missing or invalid coverage count: " + key)
    return value


def _file_coverage(path: Path, item: dict[str, object]) -> tuple[bool, str]:
    summary = object_map(item.get("summary"))
    lines, statements = integer(summary, "covered_lines"), integer(summary, "num_statements")
    branches, total = integer(summary, "covered_branches"), integer(summary, "num_branches")
    if lines > statements or branches > total:
        raise ValueError("coverage numerator exceeds denominator")
    if not isinstance(item.get("executed_branches"), list) or not isinstance(
        item.get("missing_branches"), list
    ):
        raise ValueError("missing branch data")
    if not statements and not total:
        if executable(path):
            raise ValueError("executable source reported as empty")
        return True, "EMPTY"
    count, denominator = (branches, total) if total else (lines, statements)
    return count * 100 >= denominator * 95, f"{count}/{denominator} {'branches' if total else 'lines'}"


def coverage_check(root: Path, report: Path) -> CheckResult:
    errors, details = [], []
    try:
        data = json_document(report)
        if object_map(data.get("meta")).get("branch_coverage") is not True:
            return CheckResult("coverage", ("branch-enabled JSON report required",))
        files = object_map(data.get("files"))
        normalized = {
            str((Path(name) if Path(name).is_absolute() else root / name).resolve()): value
            for name, value in files.items()
        }
        paths = owned_paths(root, coverage_layers())
        if not paths or not any(executable(path) for path in paths):
            errors.append("empty executable coverage inventory")
        for path in paths:
            name = path.relative_to(root).as_posix()
            value = normalized.get(str(path.resolve()))
            if value is None:
                errors.append(name + ": unmeasured file")
                continue
            try:
                ok, detail = _file_coverage(path, object_map(value))
                details.append(name + ": " + ("PASS " if ok else "FAIL ") + detail)
                if not ok:
                    errors.append(name + ": per-file threshold below 95%")
            except ValueError as error:
                errors.append(name + ": " + str(error))
    except (OSError, ValueError) as error:
        errors.append(str(error))
    return CheckResult("coverage", tuple(errors), tuple(details))


def artifact_check(root: Path, inventory: Mapping[str, str]) -> CheckResult:
    errors, details = [], []
    if not inventory:
        errors.append("empty artifact inventory")
    for name, expected in sorted(inventory.items()):
        try:
            path = safe_file(root, name)
            if re.fullmatch(r"[0-9a-f]{64}", expected) is None or digest(path) != expected:
                errors.append(name + ": SHA-256 mismatch")
            else:
                details.append(name + ": PASS SHA-256")
        except (OSError, ValueError) as error:
            errors.append(name + ": " + str(error))
    return CheckResult("artifacts", tuple(errors), tuple(details))


def candidate_check(
    loader: ConfigLoader, candidate: CandidateRelease, *, origin: Literal["LIVE", "REPLAY", "SIM"]
) -> CheckResult:
    result = loader.validate(candidate, origin=origin)
    return CheckResult(
        "candidate:" + origin,
        tuple(
            f"check {finding.check_id} {finding.document or 'release'} {finding.pointer}: {finding.message}"
            for finding in result.errors
        ),
        tuple("EXECUTED " + check_id for check_id in result.executed)
        + tuple("WARNING " + finding.check_id + " " + finding.pointer for finding in result.warnings),
    )


def _system_declarations(root: Path) -> dict[str, list[ast.stmt]]:
    definitions: dict[str, list[str]] = {}
    for block in re.findall(r"```python\n(.*?)```", (root / "docs/SYSTEM.md").read_text(), re.S):
        current = ""
        for line in block.splitlines():
            match = re.match(r"# crew_health/(kernel/\w+|app/registry)\.py", line)
            if match:
                current = match[1]
                definitions.setdefault(current, [])
            if line.startswith(("# crew_health/app/errors.py", "# each crew_health/")):
                current = ""
            if current:
                definitions[current].append(line)
    return {name: ast.parse("\n".join(lines)).body for name, lines in definitions.items()}


def _declaration_comparison(expected: ast.stmt, actual: ast.stmt) -> bool:
    if isinstance(expected, ast.FunctionDef) and isinstance(actual, ast.FunctionDef):
        return (
            ast.dump(expected.args) == ast.dump(actual.args)
            and (ast.dump(expected.returns) if expected.returns else None)
            == (ast.dump(actual.returns) if actual.returns else None)
            and [ast.dump(item) for item in expected.type_params]
            == [ast.dump(item) for item in actual.type_params]
        )
    if isinstance(expected, ast.TypeAlias) and isinstance(actual, ast.TypeAlias):
        return ast.dump(expected.value) == ast.dump(actual.value)
    if isinstance(expected, ast.ClassDef) and isinstance(actual, ast.ClassDef):
        members = {node.name: node for node in actual.body if isinstance(node, ast.FunctionDef)}
        fields = {
            node.target.id: ast.dump(node.annotation)
            for node in actual.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        for node in expected.body:
            if isinstance(node, ast.FunctionDef) and (
                node.name not in members or not _declaration_comparison(node, members[node.name])
            ):
                return False
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and fields.get(node.target.id) != ast.dump(node.annotation)
            ):
                return False
        return True
    return False


def interfaces_check(root: Path) -> CheckResult:
    from crew_health.app.import_contracts import check_features, check_kernel, discover

    errors, details = [], []
    for name, nodes in _system_declarations(root).items():
        path = root / "hub/src/crew_health" / (name + ".py")
        actual_nodes = ast.parse(path.read_text()).body
        actual = {
            node.name.id if isinstance(node, ast.TypeAlias) else node.name: node
            for node in actual_nodes
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.TypeAlias)
        }
        for node in nodes:
            if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.TypeAlias):
                continue
            symbol = node.name.id if isinstance(node, ast.TypeAlias) else node.name
            if symbol not in actual or not _declaration_comparison(node, actual[symbol]):
                errors.append(name + ": declaration mismatch: " + symbol)
            else:
                details.append(name + ": PASS " + symbol)
    source = root / "hub/src/crew_health"
    try:
        check_kernel(source)
        modules = discover(source, max_modules=128)
        check_features(source, tuple(name.split(".")[1] for name in modules))
    except ValueError as error:
        errors.append(str(error))
    return CheckResult("interfaces", tuple(errors), tuple(details))


def installed_outputs_check(root: Path, directory: str, base: Mapping[str, str]) -> CheckResult:
    from crew_health.contracts_gen.build import verify_overlay

    target = root / directory
    manifest = json_document(target / "manifest.json")
    outputs = object_map(manifest.get("outputs"))
    errors = []
    try:
        overlay = verify_overlay(root, base)
    except (OSError, ValueError) as error:
        return CheckResult("generated-inventory", (directory + ": " + str(error),))
    effective = dict(base) if overlay is None else overlay.effective(base)
    if (
        manifest.get("base") != dict(base)
        or manifest.get("effective") != effective
        or manifest.get("overlay") != (None if overlay is None else overlay.record())
    ):
        errors.append(directory + ": unsupported or mismatched base/overlay/effective inventory")
    if not all(isinstance(value, str) for value in outputs.values()) or not outputs:
        return CheckResult("generated-inventory", (directory + ": invalid output hash inventory",))
    hashes = {name: str(sha) for name, sha in outputs.items()}
    allowed = {"manifest.json"} | ({"__init__.py", "build.py"} if directory.startswith("hub/") else set())
    actual = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    if actual != set(hashes) | allowed:
        errors.append(directory + ": installed generated inventory differs")
    checked = artifact_check(target, hashes)
    return CheckResult("generated-inventory", tuple(errors) + checked.errors, checked.details)


def generated_check(root: Path) -> CheckResult:
    from crew_health.contracts_gen.build import build, verify_base

    errors: list[str] = []
    details: list[str] = []
    try:
        base = verify_base(root)
    except (OSError, ValueError) as error:
        return CheckResult("generated", (str(error),))
    for typescript, directory in (
        (False, "hub/src/crew_health/contracts_gen"),
        (True, "packages/contracts-ts/src/generated"),
    ):
        try:
            inventory = installed_outputs_check(root, directory, base)
            errors.extend(inventory.errors)
            details.extend(inventory.details)
            with tempfile.TemporaryDirectory(prefix="e01-generated-") as temporary:
                output = Path(temporary)
                build(root, typescript=typescript, output=output)
                expected = tuple(sorted(path for path in output.rglob("*") if path.is_file()))
                for path in expected:
                    name = path.relative_to(output).as_posix()
                    target = root / directory / name
                    if not target.is_file() or target.read_bytes() != path.read_bytes():
                        errors.append(directory + "/" + name + ": regenerated bytes differ")
                    else:
                        details.append(directory + "/" + name + ": PASS regenerated")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(directory + ": " + str(error))
    return CheckResult("generated", tuple(errors), tuple(details))


def _test_paths(root: Path) -> tuple[Path, ...]:
    directories = tuple(
        "hub/tests/" + name for name in ("kernel", "platform", "config", "clock", "events", "records", "app")
    ) + ("eval/scenarios/E01",)
    paths = {path for directory in directories for path in (root / directory).rglob("test_*.py")}
    paths.update(
        path
        for name in ("test_e01_contracts.py", "test_sha_guard.py")
        if (path := root / "hub/tests/contracts" / name).is_file()
    )
    return tuple(sorted(paths))


def trace_check(root: Path) -> CheckResult:
    design = (root / "docs/tdd/E01.md").read_text()
    promised = set(re.findall(r"\| (T-E01-\d{2}) \|", design))
    errors, details, traced = [], [], set()
    paths = _test_paths(root)
    for path in paths:
        tree = ast.parse(path.read_text())
        ids = {
            argument.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "trace"
            for argument in node.args
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        }
        traced.update(ids)
        tests = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
        ]
        if tests and not ids:
            errors.append(path.relative_to(root).as_posix() + ": missing requirement trace")
        details.append(path.relative_to(root).as_posix() + ": " + str(len(tests)) + " test functions")
    if not paths or not promised:
        errors.append("empty test or promised trace inventory")
    errors.extend("missing registered test marker " + marker for marker in sorted(promised - traced))
    for name in ("test_acceptance.py", "test_recovery.py"):
        if root / "eval/scenarios/E01" / name not in paths:
            errors.append("missing end-to-end suite " + name)
    return CheckResult("trace", tuple(errors), tuple(details))


def mutation_layers() -> tuple[tuple[str, str], ...]:
    return (
        ("platform", "application"),
        ("records", "domain"),
        ("records", "application"),
        ("events", "application"),
        ("config", "domain"),
        ("config", "application"),
    )


def mutation_inventory(root: Path) -> tuple[dict[str, str], ...]:
    mutations = []
    for path in owned_paths(root, mutation_layers()):
        name = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            kinds: tuple[str, ...] = ()
            if isinstance(node, ast.Compare):
                kinds = tuple(
                    f"compare:{index}:{type(operator).__name__}" for index, operator in enumerate(node.ops)
                )
            elif isinstance(node, ast.BoolOp):
                kinds = ("boolean:" + type(node.op).__name__,)
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                kinds = ("remove_not",)
            elif isinstance(node, ast.Constant) and type(node.value) is bool:
                kinds = ("invert_boolean",)
            for kind in kinds:
                mutations.append(
                    {
                        "id": f"{name}:{cast(ast.expr, node).lineno}:{cast(ast.expr, node).col_offset}:{kind}",
                        "source_sha256": digest(path),
                    }
                )
    return tuple(sorted(mutations, key=lambda item: item["id"]))


def _evidence(root: Path, item: Mapping[str, object]) -> bool:
    name, sha = item.get("evidence_path"), item.get("evidence_sha256")
    return isinstance(name, str) and isinstance(sha, str) and artifact_check(root, {name: sha}).ok


def mutations_check(root: Path, report: Path | None) -> CheckResult:
    inventory = mutation_inventory(root)
    if report is None:
        return CheckResult(
            "mutations", (f"missing execution evidence for {len(inventory)} required mutants",)
        )
    errors, details = [], []
    data = json_document(report)
    source_hashes = object_map(data.get("source_hashes"))
    raw = data.get("mutants")
    if not isinstance(raw, list):
        return CheckResult("mutations", ("mutation result inventory absent",))
    results = {str(item.get("id")): item for value in cast(list[object], raw) if (item := object_map(value))}
    if len(results) != len(raw) or set(results) != {item["id"] for item in inventory} or not inventory:
        errors.append("mutation result inventory differs from source inventory")
    for mutant in inventory:
        identifier = mutant["id"]
        path = identifier.split(":", 1)[0]
        item = results.get(identifier, {})
        if source_hashes.get(path) != mutant["source_sha256"]:
            errors.append(identifier + ": source hash mismatch")
        killed = (
            item.get("status") == "killed"
            and type(item.get("baseline_exit_code")) is int
            and item.get("baseline_exit_code") == 0
            and type(item.get("exit_code")) is int
            and item["exit_code"] == 1
            and isinstance(item.get("test_nodeid"), str)
        )
        equivalent = (
            item.get("status") == "equivalent"
            and isinstance(item.get("reviewer"), str)
            and bool(item.get("reviewer"))
            and isinstance(item.get("author"), str)
            and bool(item.get("author"))
            and item.get("reviewer") != item.get("author")
            and bool(item.get("reason"))
        )
        if not ((killed or equivalent) and _evidence(root, item)):
            errors.append(identifier + ": unresolved non-equivalent or unverified mutant")
        else:
            details.append(identifier + ": PASS " + str(item["status"]))
    return CheckResult("mutations", tuple(errors), tuple(details))


def condition_inventory(root: Path) -> tuple[dict[str, str], ...]:
    result: dict[str, dict[str, str]] = {}
    for path in owned_paths(root, coverage_layers()):
        name = path.relative_to(root).as_posix()
        source_hash = digest(path)
        for node in ast.walk(ast.parse(path.read_text())):
            operands: list[ast.expr] = []
            if isinstance(node, ast.If | ast.While | ast.IfExp | ast.Assert):
                operands = [node.test]
            elif isinstance(node, ast.BoolOp):
                operands = list(node.values)
            elif isinstance(node, ast.Compare):
                operands = [node]
            for operand in operands:
                if isinstance(operand, ast.BoolOp):
                    continue
                identifier = f"{name}:{operand.lineno}:{operand.col_offset}"
                result[identifier] = {"id": identifier, "source_sha256": source_hash}
    return tuple(result[key] for key in sorted(result))


def function_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, ...]:
    pending: list[ast.AST] = [function]
    result: list[ast.AST] = []
    for _ in range(100000):
        if not pending:
            return tuple(result)
        node = pending.pop()
        result.append(node)
        pending.extend(
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        )
    raise ValueError("function AST exceeds checker memory bound")


def safety_check(root: Path, report: Path | None) -> CheckResult:
    errors, details = [], []
    for path in owned_paths(root, coverage_layers()):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                errors.append(str(path.relative_to(root)) + ": runtime assert in safety module")
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                complexity = (
                    1
                    + sum(
                        1
                        for child in function_nodes(node)
                        if isinstance(
                            child,
                            ast.If | ast.IfExp | ast.For | ast.While | ast.ExceptHandler | ast.comprehension,
                        )
                    )
                    + sum(
                        len(child.values) - 1
                        for child in function_nodes(node)
                        if isinstance(child, ast.BoolOp)
                    )
                )
                if complexity > 15:
                    errors.append(
                        f"{path.relative_to(root)}:{node.lineno}: {node.name} complexity {complexity} > 15"
                    )
                for child in function_nodes(node):
                    if isinstance(child, ast.Call) and (
                        (isinstance(child.func, ast.Name) and child.func.id == node.name)
                        or (
                            isinstance(child.func, ast.Attribute)
                            and isinstance(child.func.value, ast.Name)
                            and child.func.value.id == "self"
                            and child.func.attr == node.name
                        )
                    ):
                        errors.append(f"{path.relative_to(root)}:{node.lineno}: recursion in safety function")
                details.append(f"{path.relative_to(root)}:{node.lineno}: complexity={complexity}")
    inventory = condition_inventory(root)
    if report is None:
        errors.append(f"missing true/false execution evidence for {len(inventory)} safety conditions")
    else:
        data = json_document(report)
        observed = object_map(data.get("conditions"))
        sources = object_map(data.get("source_hashes"))
        for item in inventory:
            source_name = item["id"].split(":", 1)[0]
            entry = object_map(observed.get(item["id"], {}))
            if (
                sources.get(source_name) != item["source_sha256"]
                or entry.get("outcomes") not in ([False, True], [True, False])
                or any(type(value) is not bool for value in cast(list[object], entry.get("outcomes", [])))
                or not _evidence(root, entry)
            ):
                errors.append(item["id"] + ": condition not proven true and false")
    return CheckResult("safety", tuple(errors), tuple(details))


def _package_artifacts(root: Path) -> CheckResult:
    package = root / "docs/product"
    data = json_document(package / "package-manifest.json")
    members = data.get("files")
    if not isinstance(members, list):
        return CheckResult("package", ("missing package file inventory",))
    inventory: dict[str, str] = {}
    required_root = {
        "input-dictionary.json",
        "assessment-families.json",
        "condition-assessments-119.csv",
        "sources.csv",
        "NASA-SOURCES.md",
        "DEMO-CONDITIONS.md",
        "RESEARCH-ANNEX.md",
        "environment-hazards.json",
        "routine-schedule.json",
        "responder-flows.json",
    }
    for member in cast(list[object], members):
        row = object_map(member)
        name, sha = row.get("path"), row.get("sha256")
        if not isinstance(name, str) or not isinstance(sha, str) or name in inventory:
            return CheckResult("package", ("invalid or duplicate package artifact",))
        if name.startswith("api/") or name in required_root or name.startswith("cliff/forms/"):
            inventory[name] = sha
    for name in required_root:
        if name not in inventory:
            return CheckResult("package", (name + ": required artifact absent from package manifest",))
    checked = artifact_check(package, inventory)
    return CheckResult("package", checked.errors, checked.details)


def _schema_catalog(root: Path) -> tuple[object, dict[str, dict[str, object]], tuple[str, ...]]:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    from referencing import Registry, Resource
    from referencing.exceptions import Unresolvable
    from referencing.jsonschema import DRAFT202012, Schema

    schemas = {
        path.name: json_document(path)
        for path in sorted((root / "contracts/api/schemas").glob("*.schema.json"))
    }
    resources: list[tuple[str, Resource[Schema]]] = []
    errors = []
    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
            resource: Resource[Schema] = Resource.from_contents(schema, default_specification=DRAFT202012)
            resources.extend(((name, resource), ("schemas/" + name, resource)))
        except SchemaError:
            errors.append(name + ": invalid schema")
    registry: Registry[Schema] = Registry().with_resources(resources)
    for name, schema in schemas.items():
        for pointer, value in walk(schema):
            if pointer.endswith("/$ref") and isinstance(value, str):
                try:
                    registry.resolver(base_uri=name).lookup(value)
                except Unresolvable:
                    errors.append(name + pointer + ": unresolved reference")
    return registry, schemas, tuple(errors)


def _validate_example(path: Path, registry: object) -> tuple[str, ...]:
    from jsonschema import Draft202012Validator
    from referencing import Registry
    from referencing.exceptions import Unresolvable
    from referencing.jsonschema import Schema

    wrapped = json_document(path)
    reference: object
    value: object
    if path.name == "kit-map.example.json":
        reference, value = "config-kit-map.schema.json", wrapped
    else:
        reference, value = wrapped.get("x-schema"), wrapped.get("value")
        if not isinstance(reference, str) or "value" not in wrapped:
            return ("missing example schema binding",)
    try:
        cast(Registry[Schema], registry).resolver().lookup(
            reference[5:] if reference.startswith("list:") else reference
        )
    except Unresolvable:
        return ("example schema reference cannot resolve",)
    schema: dict[str, object] = {"$ref": reference}
    if reference.startswith("list:"):
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"$ref": reference[5:]}},
                "page": {"$ref": "common.schema.json#/$defs/page"},
            },
            "required": ["items", "page"],
            "additionalProperties": False,
        }
    validator = Draft202012Validator(schema, registry=cast(Registry[Schema], registry))
    errors = []
    for index, error in enumerate(validator.iter_errors(value)):
        if index >= 1000:
            return ("example finding bound exceeded",)
        errors.append(
            "/" + "/".join(str(part) for part in error.absolute_path) + ": schema validation failed"
        )
    return tuple(errors)


def _raw_release(root: Path, *, deployment: bool) -> tuple[CandidateRelease | None, tuple[str, ...]]:
    documents: dict[ConfigName, ConfigDocumentView] = {}
    errors = []
    for name in ConfigName:
        if deployment:
            path = root / "config" / (name.value + ".json")
        elif name.value in {"environment-hazards", "routine-schedule", "responder-flows"}:
            path = root / "docs/product" / (name.value + ".json")
        elif name == ConfigName.KIT_MAP:
            path = root / "docs/product/api/examples/kit-map.example.json"
        else:
            path = root / "docs/product/api/examples/config" / (name.value + ".example.json")
        try:
            raw = path.read_bytes()
            if not deployment and name.value not in {
                "environment-hazards",
                "routine-schedule",
                "responder-flows",
                "kit-map",
            }:
                from crew_health.kernel.commands import canonical_json

                raw = canonical_json(json_document(path)["value"])
            documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
        except (OSError, ValueError) as error:
            errors.append(str(path.relative_to(root)) + ": " + str(error))
    return CandidateRelease(
        "release_corpus_deployment" if deployment else "release_corpus_examples", documents
    ), tuple(errors)


def _core_loader(root: Path) -> ConfigLoader:
    from crew_health.config.adapters.verification import ConfigArtifacts
    from crew_health.config.domain.templates import TemplateCheck

    names = (
        "input-dictionary.json",
        "sources.csv",
        "NASA-SOURCES.md",
        "DEMO-CONDITIONS.md",
        "RESEARCH-ANNEX.md",
        "environment-hazards.json",
        "routine-schedule.json",
        "responder-flows.json",
    )
    artifacts = {name: (root / "docs/product" / name).read_bytes() for name in names}
    source_documents = {
        f"api/examples/config/{name.value}.example.json": name
        for name in (ConfigName.SKILLS_CATALOG, ConfigName.ONBOARD_PLANS)
    }
    artifacts.update({name: (root / "docs/product" / name).read_bytes() for name in source_documents})
    manifest = json_document(root / "docs/product/package-manifest.json")
    members = manifest.get("files")
    if not isinstance(members, list):
        raise ValueError("missing offline artifact manifest")
    hashes = {
        str(member.get("path")): str(member.get("sha256"))
        for value in cast(list[object], members)
        if (member := object_map(value))
    }
    verified = ConfigArtifacts.from_verified(
        schema_directory=root / "contracts/api/schemas",
        artifacts=artifacts,
        inventory={name: hashes[name] for name in artifacts},
        source_documents=source_documents,
    )
    checks = LoaderChecks(maximum=1024)
    for check in (verified.schemas, verified.sources, verified.indicators, TemplateCheck()):
        checks.register(check)
    return ConfigLoader(checks, max_document_bytes=8388608)


def corpus_check(root: Path, *, loader: ConfigLoader | None = None) -> CheckResult:
    from crew_health.config.application.activation import ReleaseActivationService
    from crew_health.config.domain.errors import ConfigInvalid
    from crew_health.contracts_gen.build import verify_base

    errors: list[str] = []
    details: list[str] = []
    try:
        verify_base(root)
        checked = _package_artifacts(root)
        errors.extend(checked.errors)
        details.extend(checked.details)
        registry, schemas, schema_errors = _schema_catalog(root)
        errors.extend(schema_errors)
        errors.extend(_openapi_references(root, registry))
        details.extend(name + ": EXECUTED schema and reference check" for name in schemas)
        for path in sorted((root / "contracts/api/examples").rglob("*.json")):
            failures = _validate_example(path, registry)
            errors.extend(str(path.relative_to(root)) + ": " + error for error in failures)
            details.append(str(path.relative_to(root)) + ": " + ("FAIL" if failures else "PASS"))
        loader = loader or _core_loader(root)
        for deployment in (False, True):
            candidate, missing = _raw_release(root, deployment=deployment)
            errors.extend(missing)
            if candidate is None:
                continue
            origins: tuple[Literal["LIVE", "REPLAY", "SIM"], ...] = (
                ("LIVE",) if deployment else ("SIM", "REPLAY")
            )
            for origin in origins:
                result = candidate_check(loader, candidate, origin=origin)
                errors.extend(result.errors)
                details.extend(result.details)
            if not deployment:
                try:
                    ReleaseActivationService._live_example(candidate, "LIVE")
                except ConfigInvalid:
                    details.append("example kit: PASS LIVE rejection")
                else:
                    errors.append("example kit was not rejected in LIVE")
        units = unit_mapping_check(root)
        errors.extend(units.errors)
        details.extend(units.details)
        overlay = overlay_check(root)
        errors.extend(overlay.errors)
        details.extend(overlay.details)
    except (OSError, ValueError, KeyError, ProblemError) as error:
        errors.append(type(error).__name__ + ": " + str(error))
    return CheckResult("corpus", tuple(errors), tuple(details))


def _openapi_references(root: Path, catalog: object) -> tuple[str, ...]:
    import yaml
    from referencing import Registry, Resource
    from referencing.exceptions import Unresolvable
    from referencing.jsonschema import DRAFT202012, Schema

    registry = cast(Registry[Schema], catalog)
    openapi = object_map(yaml.safe_load((root / "contracts/api/openapi.yaml").read_bytes()))
    members = [("openapi.yaml", Resource.from_contents(openapi, default_specification=DRAFT202012))]
    for path in sorted((root / "contracts/api/examples").rglob("*.json")):
        name = path.relative_to(root / "contracts/api").as_posix()
        members.append((name, Resource.from_contents(json_document(path), default_specification=DRAFT202012)))
    registry = registry.with_resources(members)
    errors = []
    for pointer, value in walk(openapi):
        if pointer.endswith("/$ref") and isinstance(value, str):
            try:
                registry.resolver(base_uri="openapi.yaml").lookup(value)
            except Unresolvable:
                errors.append("api/openapi.yaml" + pointer + ": unresolved reference")
    return tuple(errors)


def unit_mapping_check(root: Path) -> CheckResult:
    from jsonschema import Draft202012Validator

    schema_path = root / "contracts/decision-overlay/config-unit-mapping.schema.json"
    document_path = root / "config/unit-mapping.json"
    try:
        schema, document = json_document(schema_path), json_document(document_path)
        Draft202012Validator.check_schema(schema)
        errors = tuple(
            "unit-mapping: /"
            + "/".join(str(part) for part in error.absolute_path)
            + ": schema validation failed"
            for error in Draft202012Validator(schema).iter_errors(document)
        )
        return CheckResult(
            "unit-mapping",
            errors,
            ("D12 unit-mapping validated against its overlay schema; release pinning not yet in overlay",),
        )
    except (OSError, ValueError, ProblemError) as error:
        return CheckResult("unit-mapping", (str(error),))


def overlay_check(root: Path) -> CheckResult:
    from crew_health.contracts_gen.build import OVERLAY_DIRECTORY, verify_base, verify_overlay

    if not (root / OVERLAY_DIRECTORY).exists():
        return CheckResult(
            "overlay", (), ("D19: effective contract equals guarded base; external bundles disabled",)
        )
    try:
        overlay = verify_overlay(root, verify_base(root))
    except (OSError, ValueError) as error:
        return CheckResult("overlay", ("D12 overlay rejected: " + str(error),))
    if overlay is None:
        return CheckResult("overlay", ("D12 overlay directory vanished during verification",))
    details = tuple(
        "overlay "
        + overlay.version
        + ": PASS "
        + str(trace["id"])
        + " traced to "
        + str(trace["decision"])
        + " at "
        + ", ".join(str(line) for line in cast(list[object], trace["a3"]))
        for trace in overlay.traces
    )
    return CheckResult("overlay", (), details)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "checks",
        nargs="+",
        choices=("interfaces", "generated", "corpus", "coverage", "safety", "mutations", "trace"),
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--condition-report", type=Path)
    parser.add_argument("--mutation-report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    results = []
    for name in args.checks:
        try:
            if name == "coverage":
                result = (
                    coverage_check(root, args.report)
                    if args.report is not None
                    else CheckResult(name, ("--report is required",))
                )
            elif name == "safety":
                result = safety_check(root, args.condition_report)
            elif name == "mutations":
                result = mutations_check(root, args.mutation_report)
            else:
                functions = {
                    "interfaces": interfaces_check,
                    "generated": generated_check,
                    "corpus": corpus_check,
                    "trace": trace_check,
                }
                result = functions[name](root)
        except (OSError, ValueError, ProblemError, subprocess.SubprocessError) as error:
            result = CheckResult(name, (type(error).__name__ + ": " + str(error),))
        results.append(result)
    rendered = json.dumps([asdict(result) | {"ok": result.ok} for result in results], indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())

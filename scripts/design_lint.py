"""Offline design/package checks. Explicit declarations are errors; inference is a warning.

Markdown is not a programming language. Qualified Schema.field references, API tables,
provider/consumer seam tables and source-line citations are the reliable input forms.
Unbound Literals, legacy seams and inferred package concepts are reported for review.
No reference resolver in this tool retrieves remote resources.
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import re
import shlex
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import unquote, urlparse

import yaml  # type: ignore[import-untyped]

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError
    from referencing import Registry, Resource
    from referencing.exceptions import Unresolvable
    from referencing.jsonschema import DRAFT202012, SchemaRegistry
except ImportError:
    HAS_JSONSCHEMA = False
else:
    HAS_JSONSCHEMA = True


type Severity = Literal["error", "warning"]


@dataclass(frozen=True, order=True)
class Finding:
    file: str
    line: int
    severity: Severity
    check: int
    message: str


def obj(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def seq(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def strings(value: object) -> set[str]:
    return {item for item in seq(value) if isinstance(item, str)}


def codes(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


def clean(text: str) -> str:
    return text.replace("`", "").replace("**", "").strip()


def display(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def safe_message(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"\s+-\s+", ": ", text.replace(chr(0x2013), "to").replace(chr(0x2014), ":"))


@dataclass(frozen=True)
class Section:
    number: str
    title: str
    start: int
    end: int


@dataclass(frozen=True)
class Row:
    line: int
    cells: dict[str, str]

    def get(self, *names: str) -> str:
        return next((self.cells[n] for n in names if n in self.cells), "")


@dataclass
class Document:
    path: Path
    lines: list[str]
    sections: dict[str, Section]

    @classmethod
    def read(cls, path: Path) -> Document:
        return cls.parse(path, path.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, path: Path, text: str) -> Document:
        lines = text.splitlines()
        heads: list[tuple[int, str, str, int]] = []
        fence = ""
        for n, line in enumerate(lines, 1):
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                fence = "" if fence and marker[1][0] == fence[0] else marker[1]
            match = re.match(r"^(#{2,6})\s+(\d+(?:\.\d+)*)(?:\.\s+|\s+)(.+)", line)
            if match and not fence:
                heads.append((n, match[2], match[3], len(match[1])))
        sections = {}
        for index, (n, number, title, depth) in enumerate(heads):
            end = next((h[0] for h in heads[index + 1 :] if h[3] <= depth), len(lines) + 1)
            sections[number] = Section(number, title, n, end)
        return cls(path, lines, sections)

    def part(self, number: str) -> list[tuple[int, str]]:
        section = self.sections.get(number)
        if section is None:
            return []
        return list(enumerate(self.lines[section.start : section.end - 1], section.start + 1))

    def text(self, number: str) -> str:
        return "\n".join(line for _, line in self.part(number))

    def at(self, line: int) -> str:
        matches = [s for s in self.sections.values() if s.start <= line < s.end]
        return max(matches, key=lambda s: s.start).number if matches else ""

    def rows(self, number: str) -> list[Row]:
        rows: list[Row] = []
        headers: list[str] = []
        pending: list[str] = []
        for n, line in self.part(number):
            if not line.lstrip().startswith("|"):
                headers, pending = [], []
                continue
            cells = table_cells(line)
            if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                headers = [clean(c).lower() for c in pending]
            elif headers and len(cells) == len(headers):
                rows.append(Row(n, dict(zip(headers, cells, strict=True))))
            else:
                pending = cells
        return rows


def table_cells(line: str) -> list[str]:
    """Do not split type unions or escaped pipes inside code spans."""
    cells, current, quoted, escaped = [], "", False, False
    for char in line.strip().strip("|"):
        if char == "`" and not escaped:
            quoted = not quoted
        if char == "|" and not quoted and not escaped:
            cells.append(current.strip())
            current = ""
        else:
            current += char
        escaped = char == "\\" and not escaped
    return [*cells, current.strip()]


class Mark(Protocol):
    line: int


class YamlNode(Protocol):
    id: str
    value: object
    start_mark: Mark


@dataclass
class Source:
    path: Path
    data: object
    locations: dict[tuple[str, ...], int]

    @classmethod
    def read(cls, path: Path) -> Source:
        text = path.read_text(encoding="utf-8")
        data = cast(object, yaml.load(text, Loader=yaml.CSafeLoader))
        node = cast(YamlNode | None, yaml.compose(text, Loader=yaml.CSafeLoader))
        locations: dict[tuple[str, ...], int] = {}
        stack: list[tuple[tuple[str, ...], YamlNode]] = [((), node)] if node is not None else []
        while stack:
            pointer, current = stack.pop()
            locations[pointer] = current.start_mark.line + 1
            if current.id == "mapping":
                for key, child in cast(list[tuple[YamlNode, YamlNode]], current.value):
                    stack.append(((*pointer, str(key.value)), child))
            elif current.id == "sequence":
                for i, child in enumerate(cast(list[YamlNode], current.value)):
                    stack.append(((*pointer, str(i)), child))
        return cls(path, data, locations)

    def line(self, pointer: tuple[str, ...]) -> int:
        for length in range(len(pointer), -1, -1):
            if pointer[:length] in self.locations:
                return self.locations[pointer[:length]]
        return 1


@dataclass(frozen=True)
class Schema:
    path: Path
    pointer: tuple[str, ...]
    data: dict[str, object]


def walk(value: object) -> Iterator[tuple[tuple[str, ...], dict[str, object]]]:
    stack: list[tuple[tuple[str, ...], object]] = [((), value)]
    while stack:
        pointer, current = stack.pop()
        if isinstance(current, dict):
            yield pointer, obj(current)
            stack.extend(((*pointer, k), v) for k, v in reversed(list(obj(current).items())))
        elif isinstance(current, list):
            stack.extend(((*pointer, str(i)), v) for i, v in reversed(list(enumerate(seq(current)))))


@dataclass
class Schemas:
    sources: dict[Path, Source]
    names: dict[str, Schema] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path) -> Schemas:
        sources = {p: Source.read(p) for p in sorted(directory.glob("*.json"))}
        result = cls(sources)
        for path, source in sources.items():
            root = Schema(path, (), obj(source.data))
            title = root.data.get("title")
            if isinstance(title, str) and re.fullmatch(r"\w+", title):
                result.names[title] = root
            stem = path.name.removesuffix(".schema.json")
            result.names.setdefault("".join(p.title() for p in stem.split("-")), root)
            for name, data in obj(root.data.get("$defs")).items():
                result.names.setdefault(name, Schema(path, ("$defs", name), obj(data)))
        return result

    def reference(self, schema: Schema, ref: str) -> Schema | None:
        file, _, fragment = ref.partition("#")
        if urlparse(file).scheme or file.startswith("//"):
            return None
        path = (schema.path.parent / unquote(file)).resolve() if file else schema.path
        source = self.sources.get(path)
        if source is None:
            return None
        pointer = tuple(p.replace("~1", "/").replace("~0", "~") for p in fragment.lstrip("/").split("/"))
        if not fragment:
            pointer = ()
        data = source.data
        for part in pointer:
            data = obj(data).get(part)
        return Schema(path, pointer, obj(data)) if isinstance(data, dict) else None

    def expand(self, schema: Schema) -> Schema:
        current = schema
        visited: set[tuple[Path, tuple[str, ...]]] = set()
        for _ in range(64):
            ref = current.data.get("$ref")
            if not isinstance(ref, str) or (current.path, current.pointer) in visited:
                break
            visited.add((current.path, current.pointer))
            target = self.reference(current, ref)
            if target is None:
                break
            current = Schema(
                target.path,
                target.pointer,
                {**target.data, **{k: v for k, v in current.data.items() if k != "$ref"}},
            )
        return current

    def variants(self, schema: Schema) -> list[Schema]:
        found, stack, seen = [], [schema], set()
        while stack:
            current = self.expand(stack.pop())
            key = (current.path, current.pointer)
            if key in seen:
                continue
            seen.add(key)
            found.append(current)
            for keyword in ("anyOf", "oneOf", "allOf"):
                for i, child in enumerate(seq(current.data.get(keyword))):
                    stack.append(Schema(current.path, (*current.pointer, keyword, str(i)), obj(child)))
        return found

    def lookup(self, name: str, path: str) -> list[Schema]:
        candidates = [self.names[name]] if name in self.names else []
        for part in path.split("."):
            next_candidates = []
            for parent in candidates:
                for variant in self.variants(parent):
                    prop = obj(variant.data.get("properties")).get(part.removesuffix("[]"))
                    if isinstance(prop, dict):
                        pointer = (*variant.pointer, "properties", part.removesuffix("[]"))
                        child = Schema(variant.path, pointer, obj(prop))
                        if part.endswith("[]"):
                            child = self.expand(child)
                            child = Schema(
                                child.path, (*child.pointer, "items"), obj(child.data.get("items"))
                            )
                        next_candidates.append(child)
            candidates = next_candidates
        return candidates

    def enum(self, schema: Schema, *, encoded: bool = False) -> set[str] | None:
        values = [s.data["enum"] for s in self.variants(schema) if "enum" in s.data]
        return (
            {enum_key(v) if encoded else str(v) for value in values for v in seq(value) if v is not None}
            if values
            else None
        )

    def nullable(
        self, schema: Schema, active: frozenset[tuple[Path, tuple[str, ...]]] = frozenset()
    ) -> bool | None:
        key = (schema.path, schema.pointer)
        if key in active or len(active) >= 128:
            return None
        active = active | {key}
        data = schema.data
        constraints: list[bool | None] = []
        kind = data.get("type")
        if isinstance(kind, str | list):
            constraints.append(kind == "null" if isinstance(kind, str) else "null" in seq(kind))
        if "enum" in data:
            constraints.append(None in seq(data["enum"]))
        if "const" in data:
            constraints.append(data["const"] is None)
        if isinstance(data.get("$ref"), str):
            target = self.reference(schema, str(data["$ref"]))
            constraints.append(self.nullable(target, active) if target else None)
        for keyword in ("anyOf", "oneOf", "allOf"):
            if keyword not in data:
                continue
            values = [
                self.nullable(Schema(schema.path, (*schema.pointer, keyword, str(i)), obj(v)), active)
                for i, v in enumerate(seq(data[keyword]))
            ]
            if None in values:
                constraints.append(None)
            elif keyword == "oneOf":
                constraints.append(sum(v is True for v in values) == 1)
            else:
                constraints.append((all if keyword == "allOf" else any)(values))
        if any(k in data for k in ("not", "if", "then", "else", "$dynamicRef")):
            constraints.append(None)
        return False if False in constraints else (None if None in constraints else True)


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    responses: dict[str, set[str]]


@dataclass
class Package:
    root: Path
    schemas: Schemas
    api: Source
    contracts: Document
    system: Document
    template: Document
    operations: dict[str, Operation]
    tools: dict[str, dict[str, object]]
    config: set[str]
    events: set[str]
    errors: set[str]
    requirements: dict[str, set[str]]
    ids: set[str]

    @classmethod
    def load(cls, root: Path) -> Package:
        product = root / "docs/product"
        schemas = Schemas.load(product / "api/schemas")
        api = Source.read(product / "api/openapi.yaml")
        contracts = Document.read(product / "CONTRACTS.md")
        system = Document.read(root / "docs/SYSTEM.md")
        template = Document.read(root / "docs/tdd/TEMPLATE.md")
        operations = read_operations(api)
        catalog = obj(Source.read(product / "tool-catalog.json").data)
        tools = {str(obj(t)["name"]): obj(t) for t in seq(catalog.get("tools")) if "name" in obj(t)}
        config = {token for row in contracts.rows("11") for token in codes(row.get("document"))}
        config.update(codes(system.text("5.11")))
        wire_events = {t for row in contracts.rows("18") for t in codes(row.get("type"))}
        schema_events = schemas.lookup("Event", "type")
        events = wire_events & (schemas.enum(schema_events[0]) or set()) if schema_events else set()
        events.update(system_events(system))
        errors = {
            str(v)
            for s in schemas.sources.values()
            if s.path.name == "error.schema.json"
            for _, d in walk(s.data)
            for v in seq(d.get("enum"))
            if isinstance(v, str)
        }
        requirements = backlog_requirements(Source.read(product / "backlog.json").data)
        validation = Document.read(product / "VALIDATION.md")
        prd = Document.read(product / "PRD.md")
        ids = set(re.findall(r"\b[RVD]\d{2,3}\b", "\n".join([*validation.lines, *prd.lines])))
        for document in (validation, prd):
            for line in document.lines:
                if not line.startswith("|"):
                    continue
                # A row that names an epic in its first cell is that epic's row; the other epics in it are its
                # dependencies, not owners of its requirements.
                cells = line.split("|")
                own = re.findall(r"\bE\d{2}\b", cells[1]) if len(cells) > 2 else []
                for epic in own or re.findall(r"\bE\d{2}\b", line):
                    requirements.setdefault(epic, set()).update(re.findall(r"\b[RVD]\d{2,3}\b", line))
        return cls(
            root,
            schemas,
            api,
            contracts,
            system,
            template,
            operations,
            tools,
            config,
            events,
            errors,
            requirements,
            ids,
        )


def read_operations(api: Source) -> dict[str, Operation]:
    result = {}
    for path, methods in obj(obj(api.data).get("paths")).items():
        for method, value in obj(methods).items():
            data = obj(value)
            if "operationId" not in data:
                continue
            responses = {
                str(status): strings(obj(response).get("x-error-codes"))
                for status, response in obj(data.get("responses")).items()
            }
            result[str(data["operationId"])] = Operation(method.upper(), path, responses)
    return result


def system_events(system: Document) -> set[str]:
    result = set()
    for section in ("5.7", "8"):
        for _, line in system.part(section):
            if re.search(r"\b(event|publishes|emit)\b", line, re.I) and "retired" not in line:
                result.update(re.findall(r"`([A-Z]\w+|[a-z_]+\.[a-z_]+)(?:\(|`)", line))
    return result


def backlog_requirements(data: object) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for value in seq(data):
        row = obj(value)
        epic = str(row.get("epic_id", ""))
        text = " ".join(str(row.get(k, "")) for k in ("story_id", "requirements", "acceptance", "scenarios"))
        result.setdefault(epic, set()).update(re.findall(r"\b(?:[RVD]\d{2,3}|E\d{2}-\d+)\b", text))
    return result


@dataclass
class Linter:
    package: Package
    findings: list[Finding] = field(default_factory=list)

    def add(self, doc: Document, line: int, check: int, message: str, severity: Severity = "error") -> None:
        # A proposal is not a claim about the current contract.
        if doc.at(line).split(".")[0] == "14" and check not in (12, 13, 14):
            severity = "warning"
        self.findings.append(
            Finding(display(doc.path, self.package.root), line, severity, check, safe_message(message))
        )

    def run(self, documents: list[Document], peers: list[Document]) -> list[Finding]:
        for doc in documents:
            self.structure(doc)
            self.operations(doc)
            self.fields(doc)
            self.vocabulary(doc)
            self.identifiers(doc)
            self.units(doc)
            self.coverage(doc)
            self.requests(doc)
            self.normative(doc)
            self.interims(doc)
        reconcile(self, documents, peers)
        ownership(self, documents, peers)
        return sorted(set(self.findings))

    def structure(self, doc: Document) -> None:
        checks = {"1.1": 14, "8.1": 5, "2": 10, "13": 11}
        for number, expected in self.package.template.sections.items():
            actual = doc.sections.get(number)
            exists = actual is not None
            if "." in number:
                exists = any(
                    s.title.casefold() == expected.title.casefold()
                    and s.number.startswith(number.split(".")[0] + ".")
                    for s in doc.sections.values()
                )
            if not exists:
                self.add(doc, 1, checks.get(number, 12), f"Missing section {number}: {expected.title}")

    def operations(self, doc: Document) -> None:
        endpoints = {(o.method, endpoint_shape(o.path)) for o in self.package.operations.values()}
        for n, line in enumerate(doc.lines, 1):
            for method, raw_path in re.findall(
                r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+`?(/v1/[^\s`|),;]+)", line
            ):
                path = raw_path.split("?")[0].rstrip(".")
                if (method, endpoint_shape(path)) not in endpoints:
                    ambiguous = path.count("{") != path.count("}") or "..." in raw_path
                    self.add(
                        doc, n, 1, f"Unknown endpoint {method} {path}", "warning" if ambiguous else "error"
                    )
            for name in re.findall(r"\boperationId\s*[:=]\s*[`\"']?(\w+)", line):
                if name not in self.package.operations:
                    self.add(doc, n, 1, f"Unknown operationId {name}")
        for section in doc.sections:
            if "." in section:
                continue
            for row in doc.rows(section):
                names = codes(row.get("operationid", "operation id"))
                if not names and row.get("operationid", "operation id"):
                    names = [clean(row.get("operationid", "operation id")).split()[0]]
                for name in names:
                    if name not in self.package.operations:
                        self.add(doc, row.line, 1, f"Unknown operationId {name}")
                    else:
                        self.operation_row(doc, row, name)

    def operation_row(self, doc: Document, row: Row, name: str) -> None:
        operation = self.package.operations[name]
        text = " | ".join(row.cells.values())
        endpoint = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+`?(/v1/[^\s`|),;]+)", text)
        if endpoint and (endpoint[1], endpoint_shape(endpoint[2])) != (
            operation.method,
            endpoint_shape(operation.path),
        ):
            self.add(
                doc, row.line, 1, f"{name} is {operation.method} {operation.path}, not the listed endpoint"
            )
        declared = set().union(*operation.responses.values()) if operation.responses else set()
        for code in re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", text):
            if code in self.package.errors and code not in declared:
                self.add(doc, row.line, 7, f"{name} does not declare error {code}")
        for status, code in re.findall(r"\b([45]\d\d)\s+`?([A-Z][A-Z_]+)\b", text):
            if code not in self.package.errors:
                self.add(doc, row.line, 7, f"Unknown error code {code}")
            elif code not in operation.responses.get(status, operation.responses.get("default", set())):
                self.add(doc, row.line, 7, f"{name} does not declare {status} {code}")

    def fields(self, doc: Document) -> None:
        context = ""
        for n, line in enumerate(doc.lines, 1):
            declaration = re.search(r"^\s*class\s+(\w+)", line)
            if declaration or line.startswith("#"):
                context = declaration[1] if declaration else ""
            refs = list(re.finditer(r"\b([A-Z]\w*)\.([a-z]\w*(?:\[\])?(?:\.[a-z]\w*(?:\[\])?)*)", line))
            bound = False
            for match in refs:
                if match[1] in self.package.schemas.names:
                    bound = True
                    tail = line[match.end() :]
                    if match[2] not in {"vue", "py", "ts", "json", "md"} and not tail.startswith("("):
                        self.field_use(doc, n, match[1], match[2], tail)
            if context in self.package.schemas.names:
                member = re.match(r"\s+(\w+)\s*:\s*(.+)", line)
                if member:
                    bound = True
                    self.add(
                        doc,
                        n,
                        2,
                        f"Local class {context}.{member[1]} needs an explicit wire mapping",
                        "warning",
                    )
            if "Literal[" in line and not bound:
                self.add(
                    doc, n, 3, "Literal set has no explicit Schema.field binding; enum not checked", "warning"
                )
        self.field_tables(doc)

    def field_use(self, doc: Document, n: int, name: str, path: str, tail: str) -> None:
        path = path.removeprefix("properties.").replace(".properties.", ".")
        schemas = self.package.schemas.lookup(name, path)
        if not schemas:
            method = re.match(r"(?:get|set|raise|create|read|write|save|load|delete|from|to)_", path)
            self.add(
                doc,
                n,
                2,
                f"Unknown schema field {name}.{path}" + ("; may name a local method" if method else ""),
                "warning" if method else "error",
            )
            return
        declaration = re.match(r"`?\s*:\s*`?([^;`]+)", tail)
        not_null = re.search(r"\bNOT NULL\b", tail)
        typed = declaration and re.match(
            r"(?:str|int|float|Decimal|bool|datetime|Literal\[|list\[|Sequence\[)", declaration[1]
        )
        optional = declaration and re.search(r"\b(None|null|Optional)\b", declaration[1])
        if (not_null or (typed and not optional)) and any(self.package.schemas.nullable(s) for s in schemas):
            self.add(doc, n, 2, f"{name}.{path} permits null; the declared type or column forbids it")
        enum_match = re.search(r"(?:Literal\[([^]]*)\]|\benum\s*[:=]\s*\[([^]]*)\])", tail)
        if enum_match:
            actual = literal_tokens(enum_match[1] or enum_match[2])
            if actual is None:
                self.add(
                    doc, n, 3, f"{name}.{path} enum expression is not a literal set; not checked", "warning"
                )
                return
            expected_sets = [self.package.schemas.enum(s, encoded=True) for s in schemas]
            expected = set().union(*(v for v in expected_sets if v is not None))
            if not any(v is not None for v in expected_sets):
                self.add(doc, n, 3, f"{name}.{path} has no schema enum to compare", "warning")
            elif actual != expected:
                self.add(
                    doc,
                    n,
                    3,
                    f"{name}.{path} enum differs: missing {sorted(expected - actual)}, "
                    f"extra {sorted(actual - expected)}",
                )
                for value in sorted(actual - expected):
                    near = sorted(v for v in expected if edit_distance(value, v) <= 2)
                    if near:
                        self.add(doc, n, 3, f"Possible enum spelling error {value!r}; near {near}", "warning")

    def field_tables(self, doc: Document) -> None:
        for number in doc.sections:
            if "." in number:
                continue
            for row in doc.rows(number):
                name = clean(row.get("schema"))
                path = clean(row.get("field", "column"))
                if name in self.package.schemas.names and re.fullmatch(r"\w+(?:\.\w+)*", path):
                    tail = ": " + clean(row.get("type")) + " " + row.get("nullability", "constraints")
                    if row.get("enum", "values"):
                        values = codes(row.get("enum", "values"))
                        if not values:
                            values = [v.strip(" '\"") for v in row.get("enum", "values").split(",")]
                        tail += " enum: " + repr(values)
                    self.field_use(doc, row.line, name, path, tail)

    def vocabulary(self, doc: Document) -> None:
        namespaces = {name.split(".")[0] for name in self.package.events if "." in name}
        for n, line in enumerate(doc.lines, 1):
            for token in codes(line):
                if (
                    re.fullmatch(r"[a-z_]+\.[a-z_]+", token)
                    and token.split(".")[0] in namespaces
                    and token not in self.package.events
                    and re.search(r"event|publish|consume|emit|SSE", line, re.I)
                ):
                    self.add(doc, n, 4, f"Possible unknown event {token}; verify this is an event", "warning")
                tool_field = token.split(".")
                if len(tool_field) == 2 and tool_field[0] in self.package.tools:
                    properties = obj(
                        obj(self.package.tools[tool_field[0]].get("input_schema")).get("properties")
                    )
                    if tool_field[1] not in properties:
                        self.add(doc, n, 6, f"Unknown tool payload field {token}")
            for event in re.findall(r"\bevent\s*(?:name\s*)?[:=]?\s*`([\w.]+)`", line, re.I):
                if event not in self.package.events and re.fullmatch(r"[A-Z]\w+|[a-z_]+\.[a-z_]+", event):
                    self.add(doc, n, 4, f"Unknown event {event}")
            for name in re.findall(r"\btool\s*(?:name\s*)?[:=]?\s*`(\w+)`", line, re.I):
                if name not in self.package.tools:
                    self.add(doc, n, 6, f"Unknown tool {name}")
            for code in re.findall(r"(?:\berror(?: code)?\s*[:=]?\s*`|ErrorCode\.)([A-Z][A-Z_]+)", line):
                if code not in self.package.errors:
                    self.add(doc, n, 7, f"Unknown error code {code}")
            for name in re.findall(r"\bconfiguration document\s+`([^`]+)`", line, re.I):
                self.config_name(doc, n, name)
        self.vocabulary_tables(doc)

    def config_name(self, doc: Document, n: int, token: str) -> None:
        name = Path(token).name
        if name.endswith(".schema.json") or name.endswith(".example.json"):
            return
        if not name.endswith(".json"):
            name += ".json"
        if name not in {Path(value).name for value in self.package.config}:
            self.add(doc, n, 5, f"Unknown configuration document {token}")

    def vocabulary_tables(self, doc: Document) -> None:
        for number in doc.sections:
            if "." in number:
                continue
            for row in doc.rows(number):
                if number == "8":
                    for token in codes(row.get("document", "configuration document", "config")):
                        if "/" not in token and token.endswith(".json"):
                            self.config_name(doc, row.line, token)
                        elif token + ".json" not in self.package.config and token not in self.package.config:
                            self.add(
                                doc,
                                row.line,
                                5,
                                f"Document {token} is not a named configuration document; "
                                "review whether it is a hub setting or another artifact",
                                "warning",
                            )
                for token in (
                    []
                    if any(k in row.cells for k in ("from", "to", "next", "state"))
                    else codes(row.get("event", "event name"))
                ):
                    name = token.split("(")[0]
                    if re.fullmatch(r"[\w.]+", name) and name not in self.package.events:
                        self.add(doc, row.line, 4, f"Unknown event {name}")
                for token in codes(row.get("tool", "tool name")):
                    name = token.split("(")[0]
                    if name not in self.package.tools:
                        self.add(doc, row.line, 6, f"Unknown tool {name}")
                    else:
                        fields = codes(row.get("payload fields", "input fields", "fields"))
                        valid = obj(obj(self.package.tools[name].get("input_schema")).get("properties"))
                        for key in fields:
                            if key not in valid:
                                self.add(doc, row.line, 6, f"Unknown tool payload field {name}.{key}")
                for code in codes(row.get("code", "error code")):
                    if re.fullmatch(r"[A-Z][A-Z_]+", code) and code not in self.package.errors:
                        self.add(doc, row.line, 7, f"Unknown error code {code}")

    def identifiers(self, doc: Document) -> None:
        for n, line in enumerate(doc.lines, 1):
            for name, path, template in re.findall(
                r"\b([A-Z]\w*)\.([\w.]+)\s*`?\s*(?:=|template:|generated as)\s*(?:f)?[`\"']([^`\"'\n]+)", line
            ):
                schemas = self.package.schemas.lookup(name, path)
                self.id_template(doc, n, f"{name}.{path}", schemas, template, line)
            for name, schema in self.package.schemas.names.items():
                if schema.pointer[:1] != ("$defs",) or not name.endswith("id"):
                    continue
                match = re.search(
                    r"\b" + re.escape(name) + r"`?\s+(?:template|generated as)\s*[:=]?\s*`([^`]+)`", line
                )
                if match:
                    self.id_template(doc, n, name, [schema], match[1], line)

    def id_template(
        self, doc: Document, n: int, target: str, schemas: list[Schema], template: str, line: str
    ) -> None:
        patterns = [
            s.data["pattern"]
            for parent in schemas
            for s in self.package.schemas.variants(parent)
            if isinstance(s.data.get("pattern"), str)
        ]
        for pattern_value in patterns:
            pattern = str(pattern_value)
            sample = re.sub(r"\{[^}]+\}|<[^>]+>", "x", template)
            if "{" not in template and "<" not in template:
                if re.search(pattern, template) is None:
                    self.add(doc, n, 8, f"Generated {target} value {template!r} fails {pattern}")
            else:
                self.add(
                    doc,
                    n,
                    8,
                    f"Generated {target} template needs expansion validation against {pattern}",
                    "warning",
                )
                if re.search(pattern, sample) is None:
                    self.add(doc, n, 8, f"Representative expansion {sample!r} fails {pattern}", "warning")
            if re.search(r"composite|key|derived", line, re.I):
                separators = re.findall(r"[}>]([^{}<>\s]+)[{<]", template)
                for separator in separators:
                    if re.search(pattern, f"a{separator}b"):
                        self.add(
                            doc,
                            n,
                            8,
                            f"Composite {target} separator {separator!r} is allowed in IDs; "
                            "review collision handling",
                            "warning",
                        )

    def units(self, doc: Document) -> None:
        boundary = False
        in_signature = False
        for n, line in enumerate(doc.lines, 1):
            if re.match(r"\s*class\s+", line):
                boundary = bool(re.search(r"Protocol|Port|Event|Payload", line))
            if line.startswith("#") or line.strip().startswith("```"):
                boundary = False
                in_signature = False
            explicit = boundary or (doc.at(n).split(".")[0] == "2" and "|" in line)
            explicit = explicit or bool(re.search(r"\b(port signature|event payload)\b", line, re.I))
            signature = explicit and bool(re.search(r"\bdef \w+\(|\w+\.\w+\(", line))
            in_signature = (in_signature or signature) and not line.rstrip().endswith("...")
            if re.search(r"\b(float|Decimal)\b", line) and (explicit or in_signature):
                # A Quantity elsewhere in the signature does not give this scalar a unit.
                for match in re.finditer(r"(?::|->)\s*(?:Sequence\[|list\[)?(float|Decimal)\b", line):
                    self.add(doc, n, 9, f"Bare {match[1]} crosses a port or event boundary; use Quantity")
            if ")" in line:
                in_signature = False

    def coverage(self, doc: Document) -> None:
        expected = set(self.package.requirements.get(doc.path.stem, set()))
        expected.update(re.findall(r"\b[RVD]\d{2,3}\b", doc.text("1")))
        matrix = next(
            (s.number for s in doc.sections.values() if s.title.casefold() == "acceptance matrix"), ""
        )
        rows = doc.rows(matrix) if matrix else []
        for identity in sorted(expected):
            matching = [
                r for r in rows if re.search(r"\b" + re.escape(identity) + r"\b", " ".join(r.cells.values()))
            ]
            if not matching:
                self.add(
                    doc,
                    doc.sections.get("12", Section("", "", 1, 1)).start,
                    12,
                    f"Acceptance matrix is missing {identity}",
                )
            elif not any(
                re.search(r"\b(?:T(?:EST)?[-\w]*\d|test_\w+)", " ".join(r.cells.values()), re.I)
                for r in matching
            ):
                self.add(doc, matching[0].line, 12, f"Acceptance entry {identity} has no test ID")
        for row in rows:
            for identity in re.findall(r"\b[RVD]\d{2,3}\b", " ".join(row.cells.values())):
                if identity not in self.package.ids:
                    self.add(doc, row.line, 12, f"Unknown acceptance requirement or scenario {identity}")
        tests = [
            set(re.findall(r"\b[\w-]+\b", " ".join(r.cells.values())))
            for r in doc.rows("12")
            if re.search(r"\b(?:T[-\w]*\d|test_\w+)", " ".join(r.cells.values()))
        ]
        for n, line in doc.part("15"):
            for value in marker_options(line):
                selected = [marker_selects(value, t) for t in tests]
                if None in selected or not valid_marker_expression(value):
                    self.add(
                        doc, n, 12, f"Marker expression {value!r} not checked; unsupported syntax", "warning"
                    )
                elif not any(selected):
                    self.add(doc, n, 12, f"Marker expression {value!r} selects no declared section 12 test")

    def requests(self, doc: Document) -> None:
        requests: dict[str, str] = {}
        for n, line in enumerate(doc.lines, 1):
            for request in re.findall(r"\bR-(E\d{2})-\d+[a-z]?\b", line):
                if request not in requests:
                    path = self.package.root / "docs/requests" / f"{request}.md"
                    requests[request] = path.read_text(encoding="utf-8") if path.exists() else ""
            for request, epic in re.findall(r"\b(R-(E\d{2})-\d+[a-z]?)\b", line):
                if re.search(r"(?<![\w-])" + re.escape(request) + r"(?![\w-])", requests[epic]) is None:
                    self.add(doc, n, 13, f"{request} is not filed in docs/requests/{epic}.md")
        for n, line in doc.part("14"):
            if re.search(r"\badd(?:s|ing)?\b", line, re.I):
                for name, path in re.findall(r"\b([A-Z]\w*)\.([a-z]\w*)", line):
                    if self.package.schemas.lookup(name, path):
                        self.add(
                            doc,
                            n,
                            13,
                            f"Request appears to add existing field {name}.{path}; review scope",
                            "warning",
                        )

    def normative(self, doc: Document) -> None:
        owned = owned_sections(doc)
        trace = next((s.number for s in doc.sections.values() if s.title.casefold() == "rule trace"), "1.1")
        cited = citation_lines(doc.text(trace))
        if not owned and trace in doc.sections:
            self.add(
                doc,
                doc.sections[trace].start,
                14,
                "No explicit CONTRACTS section ownership declaration; normative trace not checked",
                "warning",
            )
        for number in sorted(owned, key=lambda s: tuple(int(p) for p in s.split("."))):
            if number not in self.package.contracts.sections:
                self.add(doc, 1, 14, f"Owned CONTRACTS section {number} does not exist")
                continue
            for line, end, sentence in normative_sentences(self.package.contracts.part(number)):
                if line not in cited:
                    self.add(
                        doc,
                        doc.sections.get(trace, Section("", "", 1, 1)).start,
                        14,
                        f"Untraced CONTRACTS.md:{line} (section {number}, through line {end}): {sentence}",
                    )

    def interims(self, doc: Document) -> None:
        text = "\n".join(line for _, line in doc.part("14"))
        first = doc.sections.get("14", Section("", "", 0, 0)).start + 1
        verbs = r"\b(?:skip\w*|disable\w*|bypass\w*|pass\w*|allow\w*|treat as|if any)\b"
        safety = (
            r"\b(?:safety|dose|warning|urgent|emergency|consent|auth\w*|validat\w*|red.flag|release check)\b"
        )
        for match in re.finditer(verbs, text, re.I):
            near = text[max(0, match.start() - 100) : match.end() + 100]
            if re.search(safety, near, re.I):
                line = first + text[: match.start()].count("\n")
                self.add(doc, line, 15, f"Review interim safety wording near {match[0]!r}", "warning")


def enum_key(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def literal_tokens(text: str) -> set[str] | None:
    try:
        expression = ast.parse("[" + text + "]", mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(expression, ast.List):
        return None
    values = set()
    for item in expression.elts:
        if isinstance(item, ast.Constant):
            if item.value is not None:
                values.add(enum_key(item.value))
        elif (
            isinstance(item, ast.UnaryOp)
            and isinstance(item.op, ast.USub)
            and isinstance(item.operand, ast.Constant)
            and isinstance(item.operand.value, int | float)
        ):
            values.add(enum_key(-item.operand.value))
        else:
            return None
    return values


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def marker_options(line: str) -> list[str]:
    try:
        words = shlex.split(line.replace("`", ""))
    except ValueError:
        return []
    result: list[str] = []
    start = next((i for i, word in enumerate(words) if Path(word).name in {"pytest", "py.test"}), None)
    if start is None:
        return result
    for i in range(start + 1, len(words)):
        if words[i] in {";", "&&", "|"}:
            break
        if words[i] == "-m" and i + 1 < len(words):
            result.append(words[i + 1])
        elif words[i].startswith("-m="):
            result.append(words[i][3:])
    return result


def valid_marker_expression(expression: str) -> bool:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False
    return all(
        isinstance(
            node, ast.Expression | ast.BoolOp | ast.UnaryOp | ast.Name | ast.Load | ast.And | ast.Or | ast.Not
        )
        for node in ast.walk(tree)
    )


def marker_selects(expression: str, markers: set[str]) -> bool | None:
    if not valid_marker_expression(expression):
        return None
    tree = ast.parse(expression, mode="eval")
    values: dict[ast.AST, bool] = {}
    for node in reversed(list(ast.walk(tree))):
        if isinstance(node, ast.Name):
            values[node] = node.id in markers
        elif isinstance(node, ast.UnaryOp):
            values[node] = not values[node.operand]
        elif isinstance(node, ast.BoolOp):
            values[node] = (all if isinstance(node.op, ast.And) else any)(values[v] for v in node.values)
    return values[tree.body]


def owned_sections(doc: Document) -> set[str]:
    result = set()
    for _, line in doc.part("1"):
        plain = clean(line)
        if not re.search(
            r"\b(?:owns?|owned|ownership)\s+(?:the\s+)?CONTRACTS|"
            r"CONTRACTS(?:\.md)?\s+sections?[^|:]*\b(?:owned|ownership)\b",
            plain,
            re.I,
        ):
            continue
        if re.search(r"\b(?:does not own|not owned|owns no)\b", plain, re.I):
            continue
        match = re.search(
            r"CONTRACTS(?:\.md)?\s*(?:sections?\s*)?([\d.,\s]+(?:(?:to|and)\s+[\d.,\s]+)*)", plain, re.I
        )
        if match:
            value = re.sub(
                r"(\d+)\s+to\s+(\d+)",
                lambda m: ",".join(str(i) for i in range(int(m[1]), min(int(m[2]) + 1, 100))),
                match[1],
            )
            result.update(re.findall(r"\d+(?:\.\d+)*", value.rstrip(". ,")))
    for row in doc.rows("1"):
        value = row.get("owned contracts sections", "contracts sections owned", "owned sections")
        result.update(re.findall(r"\d+(?:\.\d+)*", clean(value)))
    return result


def citation_lines(text: str) -> set[int]:
    result: set[int] = set()
    patterns = (r"CONTRACTS(?:\.md)?(?:#L|:|\s+lines?\s+)(\d+)(?:(?:-L?|\s+to\s+)(\d+))?",)
    for pattern in patterns:
        for match in re.finditer(pattern, clean(text), re.I):
            start = int(match[1])
            stop = int(match[2]) if match[2] else start
            result.update(range(start, min(stop + 1, start + 10000)))
    for line in text.splitlines():
        cells = table_cells(line) if line.startswith("|") else []
        # A source column explicitly naming CONTRACTS may use a separate line column.
        if cells and "CONTRACTS" in cells[0]:
            for cell in cells[1:2]:
                if re.fullmatch(r"\d+(?:-\d+)?", clean(cell)):
                    bounds = [int(n) for n in clean(cell).split("-")]
                    result.update(range(bounds[0], min(bounds[-1] + 1, bounds[0] + 10000)))
    return result


def normative_sentences(lines: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """Sentence start lines are citations; wrapped sentences retain their original offsets."""
    output: list[tuple[int, int, str]] = []
    paragraph: list[tuple[int, str]] = []
    for n, line in [*lines, (0, "")]:
        if not line.strip() or line.startswith(("#", "|", "- ", "1. ")):
            output.extend(paragraph_sentences(paragraph))
            paragraph = []
        if line.strip() and not line.startswith(("#", "```")):
            paragraph.append((n, line))
    return output


def paragraph_sentences(paragraph: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    text = "\n".join(line for _, line in paragraph)
    result = []
    for match in re.finditer(r"\S[\s\S]*?(?:[.!?](?=\s+[A-Z`]|$)|$)", text):
        sentence = match[0]
        if re.search(r"\b(?:must|never|only|always|writes no)\b", sentence, re.I):
            start = paragraph[text[: match.start()].count("\n")][0]
            end = paragraph[text[: match.end()].count("\n")][0]
            result.append((start, end, " ".join(sentence.split())))
    return result


@dataclass(frozen=True)
class Seam:
    doc: Document
    line: int
    name: str
    provider: str
    consumers: frozenset[str]
    signature: str
    explicit: bool


def read_seams(doc: Document) -> list[Seam]:
    result = []
    for row in doc.rows("2"):
        name_cell = row.get("name", "port, method or event", "seam", "port", "event")
        signature = row.get("signature", "signature or payload with types", "signature or payload", "payload")
        provider_cell = row.get("provider", "provider epic", "owner")
        consumer_cell = row.get("consumers", "consumer epics", "consumer")
        explicit = bool(row.get("provider", "provider epic"))
        provider = re.findall(r"\bE\d{2}\b", provider_cell)
        consumers = set(re.findall(r"\bE\d{2}\b", consumer_cell))
        direction = row.get("direction")
        if "provides" in direction:
            provider = [doc.path.stem]
            consumers.update(re.findall(r"\bE\d{2}\b", provider_cell))
            consumers.discard(doc.path.stem)
        elif re.search(r"uses|consumes|calls|reads|handles|depends", direction):
            consumers.add(doc.path.stem)
        names = codes(name_cell)
        if not names and re.fullmatch(r"[\w.]+", clean(name_cell)):
            names = [clean(name_cell)]
        if not names:
            names = re.findall(r"`([A-Z]\w*(?:\.\w+)?|[a-z_]+\.[a-z_]+)(?:`|\()", row.get("mechanism"))
        for name in names:
            simple = name.split("(")[0]
            if not re.fullmatch(r"[\w.]+", simple):
                continue
            result.append(
                Seam(
                    doc,
                    row.line,
                    simple,
                    provider[0] if len(provider) == 1 else "",
                    frozenset(consumers),
                    signature or (name if "(" in name else ""),
                    explicit,
                )
            )
    return result


def normalize_signature(text: str) -> str:
    return re.sub(r"\s+", "", clean(text)).replace("self,", "").replace('"', "'")


def reconcile(lint: Linter, documents: list[Document], peers: list[Document]) -> None:
    seams = [seam for doc in peers for seam in read_seams(doc)]
    providers: dict[str, list[Seam]] = defaultdict(list)
    for seam in seams:
        if seam.provider == seam.doc.path.stem:
            providers[seam.name].append(seam)
    selected = {doc.path for doc in documents}
    system_text = "\n".join(lint.package.system.lines)
    retired = set(re.findall(r"`([\w.]+)`[^\n]{0,30}\bis retired\b", system_text))
    system_names = set(re.findall(r"\bclass (\w+)|`([A-Z]\w*(?:\.\w+)?)(?:`|\()", system_text))
    known = {part for pair in system_names for part in pair if part}
    for seam in seams:
        if seam.doc.path not in selected or seam.doc.path.stem not in seam.consumers:
            continue
        severity: Severity = "error" if seam.explicit else "warning"
        provided = providers.get(seam.name, [])
        if len(provided) != 1:
            lint.add(
                seam.doc,
                seam.line,
                10,
                f"Consumed seam {seam.name} has {len(provided)} provider declarations; expected exactly one",
                severity,
            )
        elif seam.provider and provided[0].doc.path.stem != seam.provider:
            lint.add(
                seam.doc,
                seam.line,
                10,
                f"{seam.name} names provider {seam.provider}, but is provided by {provided[0].doc.path.stem}",
                severity,
            )
        elif seam.signature and provided[0].signature:
            if normalize_signature(seam.signature) != normalize_signature(provided[0].signature):
                lint.add(
                    seam.doc,
                    seam.line,
                    10,
                    f"{seam.name} signature differs from "
                    f"{display(provided[0].doc.path, lint.package.root)}:{provided[0].line}",
                    severity,
                )
        elif provided:
            lint.add(
                seam.doc,
                seam.line,
                10,
                f"{seam.name} lacks a comparable signature in one seam table",
                "warning",
            )
        if seam.name in retired:
            lint.add(seam.doc, seam.line, 10, f"{seam.name} is explicitly retired by SYSTEM.md")
        elif seam.name not in known:
            near = sorted(name for name in known if edit_distance(name, seam.name) <= 2)
            if near:
                lint.add(seam.doc, seam.line, 10, f"Review {seam.name}; SYSTEM.md names {near}", "warning")


@dataclass(frozen=True)
class OwnedPath:
    doc: Document
    line: int
    pattern: str


def expand_braces(path: str) -> list[str]:
    pending, output = [path], []
    for _ in range(1024):
        if not pending:
            break
        current = pending.pop()
        match = re.search(r"\{([^{}]+)\}", current)
        if match:
            pending.extend(
                current[: match.start()] + part + current[match.end() :] for part in match[1].split(",")
            )
        else:
            output.append(current)
    return sorted(output)


def without_parentheses(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    result = []
    depth = 0
    for n, line in lines:
        quoted = False
        indent = line[: len(line) - len(line.lstrip())]
        chars = list(indent)
        for char in line[len(indent) :]:
            if char == "`":
                quoted = not quoted
            if not quoted and char == "(":
                depth += 1
            elif not quoted and char == ")" and depth:
                depth -= 1
            elif depth == 0:
                chars.append(char)
        result.append((n, "".join(chars)))
    return result


def ownership_lines(doc: Document) -> list[tuple[int, str]]:
    result = []
    other_owner = False
    for n, line in without_parentheses(doc.part("13")):
        if line.lstrip().startswith(("- ", "#", "```")) or not line.strip():
            other_owner = False
        if other_owner:
            continue
        match = re.search(r"\b(E\d{2}) owns?\b", line)
        if match and match[1] != doc.path.stem:
            result.append((n, line[: match.start()]))
            other_owner = True
        else:
            result.append((n, line))
    return result


def read_owned(doc: Document) -> list[OwnedPath]:
    output: list[OwnedPath] = []
    reading = False
    table_lines = set()
    for row in doc.rows("13"):
        table_lines.add(row.line)
        mode = row.get("access", "mode", "ownership", "action")
        if re.search(r"read|consume|never edit", mode, re.I):
            continue
        owner = row.get("owner", "epic")
        if owner and doc.path.stem not in owner:
            continue
        cell = row.get("path", "paths", "owned paths", "file", "files", "creates and owns")
        for token in codes(cell) or [clean(cell)]:
            output.extend(OwnedPath(doc, row.line, p) for p in expand_braces(token) if "/" in p)
    in_fence = False
    # A bullet's leading directory glob; bare one-segment names after it are its children.
    parent: str | None = None
    for n, line in ownership_lines(doc):
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if line.startswith("#"):
            reading = bool(re.search(r"not owned|read.only|reads|used", line, re.I))
        if re.search(r"\b(?:Reads?\b|Read.only|Not owned|Does not own|Never edits)", line, re.I):
            reading = True
        if re.match(r"\s*(?:\*\*)?(?:Owns?\b|Creates?\b)", line, re.I):
            reading = False
        if n in table_lines or line.startswith("|") or reading:
            continue
        declaration = line.split(";", 1)[0]
        bullet = declaration.lstrip().startswith("- ")
        if bullet or not declaration.startswith("  "):
            parent = None
        if in_fence:
            tokens = re.findall(r"[\w./*{},?\[\]-]+", declaration)
        elif (
            declaration.startswith("  ")
            or declaration.lstrip().startswith("- ")
            or re.fullmatch(r"\s*`[^`]+`\s*", declaration)
        ):
            tokens = codes(declaration)
        else:
            continue
        for i, token in enumerate(tokens):
            if "/" not in token or re.search(r"\s|://|[<>]", token):
                continue
            path = token
            if bullet and i == 0 and token.endswith(("/**", "/")):
                parent = token.removesuffix("**")
            elif parent is not None and not in_fence and token.rstrip("/").count("/") == 0:
                path = parent + token
            output.extend(OwnedPath(doc, n, p) for p in expand_braces(path))
    return output


def glob_regex(pattern: str) -> str:
    pattern = pattern.removeprefix("./")
    if pattern.endswith("/"):
        pattern += "**"
    tokens = re.findall(r"\*\*/|\*\*|\*|\?|\[[^]]+\]|[^*?\[]+|\[", pattern)
    replacements = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}
    return (
        "^"
        + "".join(
            replacements.get(t, t if t.startswith("[") and t.endswith("]") else re.escape(t)) for t in tokens
        )
        + "$"
    )


def overlap(left: str, right: str) -> bool | None:
    if left == right:
        return True
    samples = {left, right}
    for pattern in (left, right):
        for fill in ("x", "", "x.py", "sub/x.py"):
            sample = re.sub(r"\*\*|\*|\?", fill, pattern)
            sample = re.sub(r"\[([^]]+)\]", lambda m: m[1][0], sample)
            samples.add(sample + "x" if sample.endswith("/") else sample)
    if any(re.fullmatch(glob_regex(left), s) and re.fullmatch(glob_regex(right), s) for s in samples):
        return True
    prefixes = [re.split(r"[*?\[]", p)[0] for p in (left, right)]
    if any(c in left + right for c in "*?[") and (
        prefixes[0].startswith(prefixes[1]) or prefixes[1].startswith(prefixes[0])
    ):
        return None
    return False


def ownership(lint: Linter, documents: list[Document], peers: list[Document]) -> None:
    selected = {doc.path for doc in documents}
    paths = [p for doc in peers for p in read_owned(doc)]
    buckets: dict[str, list[OwnedPath]] = defaultdict(list)
    for path in paths:
        buckets[path.pattern.split("/")[0]].append(path)
    for bucket in buckets.values():
        for left, right in itertools.combinations(bucket, 2):
            if left.doc.path == right.doc.path or not {left.doc.path, right.doc.path} & selected:
                continue
            matches = overlap(left.pattern, right.pattern)
            if matches is False:
                continue
            for own, other in ((left, right), (right, left)):
                if own.doc.path in selected:
                    lint.add(
                        own.doc,
                        own.line,
                        11,
                        f"Owned path {own.pattern} "
                        f"{'overlaps' if matches else 'may overlap'} {other.pattern} in "
                        f"{display(other.doc.path, lint.package.root)}:{other.line}",
                        "error" if matches else "warning",
                    )


@dataclass(frozen=True)
class Example:
    source: Source
    pointer: tuple[str, ...]
    value: object
    schema: Schema | None


def validate_example(package: Package, example: Example) -> list[tuple[Severity, str]]:
    if example.schema is None:
        return [("warning", "Example has no explicit schema association; not checked")]
    if not HAS_JSONSCHEMA:
        return [("warning", "jsonschema is unavailable; example validation not checked")]
    resources: list[tuple[str, Resource[bool | Mapping[str, object]]]] = []
    for source in package.schemas.sources.values():
        data = {**obj(source.data), "$id": source.path.as_uri()}
        resources.append(
            (source.path.as_uri(), Resource.from_contents(data, default_specification=DRAFT202012))
        )
    # Registry defaults to refusing retrieval, including HTTP and file URLs.
    registry = cast(SchemaRegistry, Registry().with_resources(resources))
    schema = {"$ref": example.schema.path.as_uri() + "#/" + "/".join(example.schema.pointer)}
    if not example.schema.pointer:
        schema["$ref"] = example.schema.path.as_uri()
    if example.schema.path not in package.schemas.sources:
        schema = cast(dict[str, str], example.schema.data)
    try:
        validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(example.value), key=lambda e: (str(list(e.path)), e.message))
    except (Unresolvable, SchemaError, ValueError, TypeError) as error:
        return [("warning", f"Example validation not checked: {type(error).__name__}")]
    return [
        ("error", f"Example /{'/'.join(str(p) for p in e.path)} ({e.validator}): {e.message}") for e in errors
    ]


def file_example(package: Package, source: Source, schema: Schema | None) -> Example:
    data = obj(source.data)
    if "value" in data and ("x-schema" in data or "summary" in data or "description" in data):
        explicit = data.get("x-schema")
        if schema is None and isinstance(explicit, str):
            base = package.root / "docs/product/api/schemas"
            path = (base / explicit.split("#", 1)[0]).resolve()
            target = package.schemas.sources.get(path)
            if target:
                schema = Schema(path, (), obj(target.data))
                if "#" in explicit:
                    schema = package.schemas.reference(schema, explicit) or schema
        return Example(source, ("value",), data["value"], schema)
    return Example(source, (), source.data, schema)


def endpoint_shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{parameter}", path.split("?", 1)[0].rstrip("."))


def package_examples(package: Package) -> list[Example]:
    examples = []
    sources = {package.api.path: package.api, **package.schemas.sources}
    seen_files: set[Path] = set()
    for source in list(sources.values()):
        for pointer, data in walk(source.data):
            schema_data = obj(data.get("schema"))
            schema = Schema(source.path, pointer, data)
            if schema_data:
                schema = Schema(source.path, (*pointer, "schema"), schema_data)
            ref = schema.data.get("$ref")
            if isinstance(ref, str):
                schema = package.schemas.reference(schema, ref) or schema
            if "example" in data:
                examples.append(Example(source, (*pointer, "example"), data["example"], schema))
            values = data.get("examples")
            if isinstance(values, list):
                examples.extend(
                    Example(source, (*pointer, "examples", str(i)), value, schema)
                    for i, value in enumerate(seq(values))
                )
            for name, raw in obj(values).items():
                value = obj(raw)
                if "$ref" in value:
                    path = local_path(source.path, str(value["$ref"]), package.root)
                    if path is not None and path.is_file():
                        sample = sources.setdefault(path, Source.read(path))
                        seen_files.add(path)
                        examples.append(file_example(package, sample, schema if schema_data else None))
                elif "value" in value:
                    examples.append(
                        Example(source, (*pointer, "examples", name, "value"), value["value"], schema)
                    )
    for path in sorted((package.root / "docs/product").rglob("*.example.json")):
        if path not in seen_files:
            schema_path = (
                package.root
                / "docs/product/api/schemas"
                / ("config-" + path.name.replace(".example", ".schema"))
            )
            source = Source.read(path)
            target = package.schemas.sources.get(schema_path)
            examples.append(
                file_example(package, source, Schema(schema_path, (), obj(target.data)) if target else None)
            )
            seen_files.add(path)
    for path in sorted((package.root / "docs/product/api/examples").rglob("*.json")):
        if path not in seen_files:
            source = Source.read(path)
            examples.append(file_example(package, source, None))
    return examples


def local_path(base: Path, reference: str, root: Path) -> Path | None:
    if urlparse(reference).scheme or reference.startswith("//"):
        return None
    path = (base.parent / unquote(reference.split("#", maxsplit=1)[0])).resolve()
    return path if path.is_relative_to(root) else None


def package_fields(package: Package) -> list[Finding]:
    groups: dict[str, list[tuple[Schema, bool]]] = defaultdict(list)
    for source in package.schemas.sources.values():
        for pointer, data in walk(source.data):
            required = strings(data.get("required"))
            for name, value in obj(data.get("properties")).items():
                if name in {"state", "status", "type", "kind", "value", "name", "id", "code", "source"}:
                    # These names describe unrelated concepts; a common spelling proves nothing.
                    title = str(obj(source.data).get("title", source.path.stem))
                    key = re.sub(r"(?:Create|Update|View|Response|Request)$", "", title) + "." + name
                else:
                    key = name
                groups[key].append(
                    (Schema(source.path, (*pointer, "properties", name), obj(value)), name in required)
                )
    findings = []
    for name, fields in sorted(groups.items()):
        enums = {tuple(sorted(v)) for s, _ in fields if (v := package.schemas.enum(s)) is not None}
        nullable = {package.schemas.nullable(s) for s, _ in fields}
        types = {
            tuple(sorted(strings(s.data.get("type"))))
            if isinstance(s.data.get("type"), list)
            else (str(s.data.get("type")),)
            for f, _ in fields
            for s in [package.schemas.expand(f)]
        }
        messages = []
        if True in nullable and any(
            required and package.schemas.nullable(s) is False for s, required in fields
        ):
            messages.append(
                (2, f"Inferred concept {name}: nullable in one schema, required and non-null in another")
            )
        if len(types) > 1 and len({s.path for s, _ in fields}) > 1:
            messages.append(
                (2, f"Inferred concept {name}: field type declarations differ; review intended contexts")
            )
        if len(enums) > 1:
            messages.append(
                (3, f"Inferred concept {name}: enum sets differ; review whether these are the same concept")
            )
        for check, message in messages:
            for schema, _ in fields:
                source = package.schemas.sources[schema.path]
                findings.append(
                    Finding(
                        display(source.path, package.root),
                        source.line(schema.pointer),
                        "warning",
                        check,
                        message,
                    )
                )
    return findings


def placeholder_values(value: object) -> Iterator[tuple[tuple[str, ...], str]]:
    stack: list[tuple[tuple[str, ...], object]] = [((), value)]
    while stack:
        pointer, current = stack.pop()
        if isinstance(current, str) and re.search(
            r"<[^>]+>|\$\{[^}]+\}|\b(?:TODO|TBD|PLACEHOLDER)\b|^\.{3}$", current
        ):
            yield pointer, current
        elif isinstance(current, dict):
            stack.extend(((*pointer, k), v) for k, v in obj(current).items())
        elif isinstance(current, list):
            stack.extend(((*pointer, str(i)), v) for i, v in enumerate(seq(current)))


def lint_package(package: Package) -> list[Finding]:
    findings = package_fields(package)
    for example in package_examples(package):
        file = display(example.source.path, package.root)
        for pointer, value in placeholder_values(example.value):
            findings.append(
                Finding(
                    file,
                    example.source.line((*example.pointer, *pointer)),
                    "warning",
                    8,
                    safe_message(f"Possible placeholder in example: {value!r}"),
                )
            )
        for severity, message in validate_example(package, example):
            findings.append(
                Finding(file, example.source.line(example.pointer), severity, 8, safe_message(message))
            )
    return sorted(set(findings))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--json", action="store_true", help="emit only a JSON list of findings")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--all", action="store_true", help="check every docs/tdd/Exx.md")
    modes.add_argument("--package", action="store_true", help="check package schema consistency and examples")
    result.add_argument("paths", nargs="*", type=Path)
    return result


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    arguments = parser().parse_args(argv)
    if (arguments.all or arguments.package) and arguments.paths:
        parser().error("explicit paths cannot be combined with --all or --package")
    if not (arguments.all or arguments.package or arguments.paths):
        parser().error("choose --all, --package, or one or more design paths")
    root = (root or Path(__file__).resolve().parent.parent).resolve()
    try:
        package = Package.load(root)
        if arguments.package:
            findings = lint_package(package)
        else:
            paths = sorted((root / "docs/tdd").glob("E[0-9][0-9].md"))
            requested = paths if arguments.all else [(root / p).resolve() for p in arguments.paths]
            peers = {p: Document.read(p) for p in sorted(set(paths + requested))}
            findings = Linter(package).run([peers[p] for p in sorted(set(requested))], list(peers.values()))
    except (OSError, ValueError, yaml.YAMLError) as error:
        findings = [
            Finding(
                display(root, root) or ".", 1, "error", 1, safe_message(f"Input could not be read: {error}")
            )
        ]
    if arguments.json:
        print(json.dumps([asdict(finding) for finding in findings], indent=2, ensure_ascii=True))
    else:
        for finding in findings:
            print(
                f"{finding.file}:{finding.line}: {finding.severity}: "
                f"[check {finding.check}] {finding.message}"
            )
        counts = Counter((f.check, f.severity) for f in findings)
        for check in range(1, 16):
            print(f"check {check}: {counts[check, 'error']} error(s), {counts[check, 'warning']} warning(s)")
    return int(any(f.severity == "error" for f in findings))


if __name__ == "__main__":
    raise SystemExit(main())

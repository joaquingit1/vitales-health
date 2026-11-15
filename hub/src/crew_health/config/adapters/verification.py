from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012, Schema

from crew_health.config.domain.checks import IndicatorCheck, SourceCheck, parse_document
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding


def verified_documents(
    documents: Mapping[ConfigName, bytes], inventory: Mapping[ConfigName, str]
) -> dict[ConfigName, ConfigDocumentView]:
    if documents.keys() != inventory.keys():
        raise ConfigInvalid("configuration inventory mismatch")
    result = {}
    for name, raw in sorted(documents.items()):
        digest = hashlib.sha256(raw).hexdigest()
        if digest != inventory[name]:
            raise ConfigInvalid("configuration hash mismatch")
        result[name] = ConfigDocumentView(name, digest, raw)
    return result


@dataclass(frozen=True, slots=True)
class SchemaCheck:
    validators: Mapping[ConfigName, Draft202012Validator]
    check_id: str = "1"

    @classmethod
    def from_directory(cls, directory: Path) -> SchemaCheck:
        resources: list[tuple[str, Resource[Schema]]] = []
        schemas: dict[str, dict[str, object]] = {}
        paths = tuple(sorted(directory.glob("*.schema.json")))
        if not paths or len(paths) > 1024:
            raise ConfigInvalid("schema inventory invalid")
        for path in paths:
            value = parse_document(path.read_bytes())
            if not isinstance(value, dict):
                raise ConfigInvalid("schema is not an object")
            schema = cast(dict[str, object], value)
            Draft202012Validator.check_schema(schema)
            schemas[path.name] = schema
            resources.append((path.name, Resource.from_contents(schema, default_specification=DRAFT202012)))
        registry: Registry[Schema] = Registry().with_resources(resources)
        validators = {}
        for name in ConfigName:
            config_schema = schemas.get(f"config-{name.value}.schema.json")
            if config_schema is None:
                raise ConfigInvalid("required configuration schema absent")
            validators[name] = Draft202012Validator(config_schema, registry=registry)
        return cls(validators)

    def check_document(self, document: ConfigDocumentView) -> tuple[Finding, ...]:
        validator = self.validators.get(document.name)
        if validator is None:
            return (Finding(self.check_id, document.name, "/", "schema unavailable"),)
        try:
            value = parse_document(document.raw)
        except ConfigInvalid:
            return (Finding(self.check_id, document.name, "/", "invalid JSON document"),)
        errors = []
        for index, error in enumerate(validator.iter_errors(value)):
            if index >= 1000:
                raise ConfigInvalid("schema finding bound exceeded")
            pointer = "/" + "/".join(
                str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path
            )
            errors.append(Finding(self.check_id, document.name, pointer, "document does not match schema"))
        return tuple(errors)

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        return tuple(
            finding
            for _, document in sorted(candidate.documents.items())
            for finding in self.check_document(document)
        )


@dataclass(frozen=True, slots=True)
class ConfigArtifacts:
    schemas: SchemaCheck
    sources: SourceCheck
    indicators: IndicatorCheck

    @classmethod
    def from_verified(
        cls,
        *,
        schema_directory: Path,
        artifacts: Mapping[str, bytes],
        inventory: Mapping[str, str],
        source_documents: Mapping[str, ConfigName] | None = None,
    ) -> ConfigArtifacts:
        required = {
            "input-dictionary.json",
            "sources.csv",
            "NASA-SOURCES.md",
            "DEMO-CONDITIONS.md",
            "RESEARCH-ANNEX.md",
            "environment-hazards.json",
            "routine-schedule.json",
            "responder-flows.json",
        }
        if set(artifacts) != set(inventory) or not required <= artifacts.keys():
            raise ConfigInvalid("offline source inventory incomplete")
        for name, raw in sorted(artifacts.items()):
            if len(raw) > 8388608 or hashlib.sha256(raw).hexdigest() != inventory[name]:
                raise ConfigInvalid("offline source hash or bound invalid")
        schemas = SchemaCheck.from_directory(schema_directory)
        sources = _source_identifiers(artifacts)
        sources.update(_contributor_sources(schemas, artifacts, source_documents or {}))
        return cls(
            schemas,
            SourceCheck(frozenset(sources)),
            IndicatorCheck(_dictionary_fields(artifacts["input-dictionary.json"])),
        )


def _dictionary_fields(raw: bytes) -> frozenset[str]:
    dictionary = parse_document(raw)
    if not isinstance(dictionary, list):
        raise ConfigInvalid("input dictionary invalid")
    fields = set()
    for item in cast(list[object], dictionary):
        if not isinstance(item, dict) or not isinstance(item.get("field_id"), str):
            raise ConfigInvalid("input dictionary entry invalid")
        fields.add(item["field_id"])
    return frozenset(fields)


def _source_identifiers(artifacts: Mapping[str, bytes]) -> set[str]:
    import csv
    import io
    import re

    sources = set()
    for row in csv.DictReader(io.StringIO(artifacts["sources.csv"].decode("utf-8-sig"))):
        if not row.get("source_id"):
            raise ConfigInvalid("source inventory row invalid")
        sources.add(row["source_id"])
    for name, raw in sorted(artifacts.items()):
        if name.endswith(".md"):
            sources.update(re.findall(r"(?m)^\|\s*((?:EV|RT|RS|SK|S|C|N|L)\d{2})\s*\|", raw.decode()))
        if name in {"environment-hazards.json", "routine-schedule.json", "responder-flows.json"}:
            value = parse_document(raw)
            if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
                raise ConfigInvalid("research source inventory missing")
            for source in cast(list[object], value["sources"]):
                if not isinstance(source, dict) or not isinstance(source.get("id"), str):
                    raise ConfigInvalid("research source row invalid")
                sources.add(source["id"])
    return sources


def _contributor_sources(
    schemas: SchemaCheck, artifacts: Mapping[str, bytes], bindings: Mapping[str, ConfigName]
) -> set[str]:
    from crew_health.kernel.commands import canonical_json

    sources = set()
    for name, config_name in sorted(bindings.items()):
        if name not in artifacts:
            raise ConfigInvalid("contributor source artifact missing")
        value = parse_document(artifacts[name])
        if name.endswith(".example.json"):
            if (
                not isinstance(value, dict)
                or value.get("x-schema") != f"config-{config_name.value}.schema.json"
            ):
                raise ConfigInvalid("contributor source schema binding invalid")
            value = value.get("value")
        raw = canonical_json(value)
        document = ConfigDocumentView(config_name, hashlib.sha256(raw).hexdigest(), raw)
        if schemas.check_document(document):
            raise ConfigInvalid("contributor source document invalid")
        if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
            raise ConfigInvalid("contributor source metadata missing")
        for source in cast(list[object], value["sources"]):
            if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
                raise ConfigInvalid("contributor source metadata invalid")
            sources.add(source["source_id"])
    return sources

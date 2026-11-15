"""Shared fixtures: the simulator documents and E23's contract snapshot (tests/fixtures/e23/SNAPSHOT.json)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import Schema

from crew_health_sim_habitat.app import Loaded, load_all

PROJECT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT.parents[1]
E23 = PROJECT / "tests" / "fixtures" / "e23"
CATALOG = E23 / "config" / "sensor-catalog.json"

type Validate = Callable[[object], None]


def read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return cast(dict[str, object], json.load(handle))


@pytest.fixture(scope="session")
def loaded() -> Loaded:
    return load_all(REPO_ROOT, catalog_path=CATALOG)


@pytest.fixture(scope="session")
def schema_registry() -> Registry[Schema]:
    registry: Registry[Schema] = Registry()
    for path in sorted((E23 / "schemas").glob("*.schema.json")):
        schema = read_json(path)
        registry = registry.with_resource(str(schema["$id"]), Resource.from_contents(schema))
    return registry


def _validator(registry: Registry[Schema], ref: str) -> Validate:
    validator = Draft202012Validator({"$ref": ref}, registry=registry, format_checker=FormatChecker())

    def validate(instance: object) -> None:
        validator.validate(instance)

    return validate


@pytest.fixture(scope="session")
def validate_batch(schema_registry: Registry[Schema]) -> Validate:
    return _validator(schema_registry, "requests.schema.json#/$defs/AdapterReadingBatch")


@pytest.fixture(scope="session")
def validate_channel(schema_registry: Registry[Schema]) -> Validate:
    return _validator(schema_registry, "sensor-channel.schema.json")


@pytest.fixture(scope="session")
def validate_ingest_result(schema_registry: Registry[Schema]) -> Validate:
    return _validator(schema_registry, "ingest-result.schema.json")

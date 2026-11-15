from __future__ import annotations

import json
from math import isfinite
from decimal import Decimal
from pathlib import Path

from crew_health.kernel.units import Quantity, Unit
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from crew_health_bench.application.gate import validate_gate_configuration
from crew_health_bench.domain.documents import integer, object_map, text
from crew_health_bench.domain.errors import BenchError


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def read_json(path: Path, *, max_file_bytes: int) -> dict[str, object]:
    if max_file_bytes < 1:
        raise BenchError("case_invalid")
    try:
        with path.open("rb") as source:
            data = source.read(max_file_bytes + 1)
        if len(data) > max_file_bytes:
            raise BenchError("case_invalid", instance=str(path))
        return decode_json(data)
    except OSError as error:
        raise BenchError("target_unreadable", instance=str(path), exit_code=6) from error
    except (ValueError, UnicodeError, RecursionError) as error:
        raise BenchError("case_invalid", instance=str(path)) from error


def decode_json(data: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite,
                                   parse_float=_finite_float)
        return object_map(value)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise BenchError("case_invalid") from error


def _nonfinite(value: str) -> object:
    raise ValueError(value)


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError("nonfinite number")
    return parsed


def load_document(
    path: Path, *, max_file_bytes: int, schema_path: Path | None = None
) -> dict[str, object]:
    if schema_path is None:
        schema_path = path.parent / "schemas" / (path.stem + ".schema.json")
    document = read_json(path, max_file_bytes=max_file_bytes)
    schema = read_json(schema_path, max_file_bytes=max_file_bytes)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(document)
    except (ValidationError, SchemaError) as error:
        raise BenchError("case_invalid", instance=str(path)) from error
    if path.name == "suites.json":
        validate_gate_configuration(object_map(document["release_gates"]))
    return document


class BenchSettings:
    def __init__(self, document: dict[str, object]) -> None:
        self._document = document.copy()

    def integer(self, key: str) -> int:
        return integer(self._row(key)["value"])

    def quantity(self, key: str) -> Quantity:
        row = self._row(key)
        return Quantity(Decimal(integer(row["value"])), Unit(text(row["unit"])))

    def boolean(self, key: str) -> bool:
        value = self._row(key)["value"]
        if not isinstance(value, bool):
            raise BenchError("case_invalid")
        return value

    def text(self, key: str) -> str:
        return text(self._row(key)["value"])

    def _row(self, key: str) -> dict[str, object]:
        if key not in self._document:
            raise BenchError("case_invalid")
        return object_map(self._document[key])

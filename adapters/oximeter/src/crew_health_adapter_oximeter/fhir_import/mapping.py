"""Schema-validated, immutable clinical mappings with provenance, never guessed codes."""

import json
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .domain import ImportMapping, ImportMappingError, InterpretationRule, NumericRule


def mapping_document(mapping: ImportMapping) -> dict[str, object]:
    return {
        "version": mapping.version,
        "provenance": [{"path": path, "note": note} for path, note in mapping.provenance],
        "numeric": [
            {
                "system": row.system,
                "code": row.code,
                "indicator_code": row.indicator_code,
                "accepted_ucum": list(row.accepted_ucum),
            }
            for row in mapping.numeric
        ],
        "interpretations": [
            {"system": row.system, "code": row.code, "finding_kind": row.finding_kind}
            for row in mapping.interpretations
        ],
    }


def parse_mapping(document: Mapping[str, object] | ImportMapping) -> ImportMapping:
    if isinstance(document, ImportMapping):
        document = mapping_document(document)
    schema = json.loads(
        files("crew_health_adapter_oximeter")
        .joinpath("schemas", "clinical-import-mapping.schema.json")
        .read_text(encoding="utf-8")
    )
    try:
        Draft202012Validator(schema).validate(document)
    except ValidationError as exc:
        pointer = "/" + "/".join(
            str(part).replace("~", "~0").replace("/", "~1") for part in exc.absolute_path
        )
        raise ImportMappingError(pointer, "schema_invalid") from exc
    numeric_rows = cast(list[dict[str, object]], document["numeric"])
    interpretation_rows = cast(list[dict[str, str]], document["interpretations"])
    seen: set[tuple[str, str]] = set()
    for group, rows in (("numeric", numeric_rows), ("interpretations", interpretation_rows)):
        for index, entry in enumerate(rows):
            row = cast(Mapping[str, object], entry)
            identity = (cast(str, row["system"]), cast(str, row["code"]))
            if identity in seen:
                raise ImportMappingError(f"/{group}/{index}", "duplicate_code")
            seen.add(identity)
    return ImportMapping(
        cast(str, document["version"]),
        tuple((row["path"], row["note"]) for row in cast(list[dict[str, str]], document["provenance"])),
        tuple(
            NumericRule(
                cast(str, row["system"]),
                cast(str, row["code"]),
                cast(str, row["indicator_code"]),
                tuple(cast(list[str], row["accepted_ucum"])),
            )
            for row in numeric_rows
        ),
        tuple(
            InterpretationRule(row["system"], row["code"], row["finding_kind"]) for row in interpretation_rows
        ),
    )


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _no_constant(value: str) -> object:
    raise ValueError("nonfinite")


def load_mapping_file(path: Path, max_bytes: int) -> ImportMapping:
    if type(max_bytes) is not int or not 1 <= max_bytes <= 10485760:
        raise ImportMappingError("/", "invalid_max_bytes")
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ImportMappingError("/", "too_large")
        document: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_fields, parse_constant=_no_constant
        )
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ImportMappingError("/", "malformed") from exc
    if not isinstance(document, dict):
        raise ImportMappingError("/", "object_required")
    return parse_mapping(cast(Mapping[str, object], document))

from __future__ import annotations

import json
from pathlib import Path

from crew_health.kernel.commands import canonical_json
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from crew_health_bench.adapters.documents import read_json
from crew_health_bench.domain.errors import BenchError


class ArtifactSchemas:
    def __init__(self, root: Path, max_file_bytes: int) -> None:
        self._validators: dict[str, Draft202012Validator] = {}
        for name in ("run-manifest", "case-results", "aggregate"):
            schema = read_json(root / (name + ".schema.json"), max_file_bytes=max_file_bytes)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as error:
                raise BenchError("case_invalid") from error
            self._validators[name] = Draft202012Validator(schema)

    def validate(self, name: str, document: object) -> None:
        if name not in self._validators:
            raise BenchError("case_invalid")
        try:
            self._validators[name].validate(json.loads(canonical_json(document)))
        except ValidationError as error:
            raise BenchError("case_invalid") from error

"""Bounded configuration reads and schema validation before activation."""

import json
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from crew_health_device.domain.errors import ProfileError
from crew_health_device.domain.profile import DeviceProfile, parse_profile


def validate_document(document: Mapping[str, object], schema_name: str) -> None:
    schema = json.loads(
        files("crew_health_device").joinpath("schemas", schema_name).read_text(encoding="utf-8")
    )
    try:
        Draft202012Validator(schema).validate(document)
    except ValidationError as exc:
        pointer = "/" + "/".join(str(part) for part in exc.absolute_path)
        raise ProfileError(pointer, "schema_invalid") from exc


def load_document(path: Path, max_bytes: int) -> Mapping[str, object]:
    try:
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ProfileError("/", "oversize")
        document: object = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ProfileError("/", "unreadable_document") from exc
    if not isinstance(document, dict):
        raise ProfileError("/", "object_required")
    return cast(Mapping[str, object], document)


def load_profile_file(path: Path, max_bytes: int) -> DeviceProfile:
    document = load_document(path, max_bytes)
    validate_document(document, "device-profile.schema.json")
    return parse_profile(document)

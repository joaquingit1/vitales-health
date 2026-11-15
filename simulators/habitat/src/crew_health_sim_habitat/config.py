"""Load and validate the simulator's configuration documents.

Every number in the documents is a parameter object {value, unit, basis, source_ids}. The loader
checks the unit each field expects (no silent conversion), converts time to seconds once, and
rejects a document that is missing anything. Nothing here falls back to a default value.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from crew_health_sim_habitat.errors import ConfigError

type JsonObject = dict[str, object]

_SECONDS = {"s": 1.0, "min": 60.0, "h": 3600.0}


def load_json(path: Path) -> JsonObject:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} is not a JSON object")
    return cast(JsonObject, data)


def obj(data: Mapping[str, object], key: str) -> JsonObject:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key!r} must be an object")
    return cast(JsonObject, value)


def opt_obj(data: Mapping[str, object], key: str) -> JsonObject | None:
    return None if data.get(key) is None else obj(data, key)


def arr(data: Mapping[str, object], key: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ConfigError(f"{key!r} must be a list")
    return cast(list[object], value)


def objs(data: Mapping[str, object], key: str) -> list[JsonObject]:
    items = arr(data, key)
    if not all(isinstance(item, dict) for item in items):
        raise ConfigError(f"every item of {key!r} must be an object")
    return cast(list[JsonObject], items)


def text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key!r} must be a non-empty string")
    return value


def opt_text(data: Mapping[str, object], key: str) -> str | None:
    return None if data.get(key) is None else text(data, key)


def number(data: Mapping[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ConfigError(f"{key!r} must be a finite number")
    return float(value)


@dataclass(frozen=True, slots=True)
class Param:
    value: float
    unit: str
    basis: str
    source_ids: tuple[str, ...]


def param(data: Mapping[str, object], key: str, unit: str) -> Param:
    raw = obj(data, key)
    found = text(raw, "unit")
    if found != unit:
        raise ConfigError(f"{key!r} must be in {unit!r}, found {found!r}")
    sources = arr(raw, "source_ids")
    if not all(isinstance(s, str) and s for s in sources):
        raise ConfigError(f"{key!r} source_ids must be strings")
    return Param(number(raw, "value"), found, text(raw, "basis"), tuple(cast(list[str], sources)))


def value(data: Mapping[str, object], key: str, unit: str) -> float:
    return param(data, key, unit).value


def seconds(data: Mapping[str, object], key: str) -> float:
    raw = obj(data, key)
    unit = text(raw, "unit")
    if unit not in _SECONDS:
        raise ConfigError(f"{key!r} must be in s, min or h, found {unit!r}")
    result = value(data, key, unit) * _SECONDS[unit]
    if result < 0:
        raise ConfigError(f"{key!r} must not be negative")
    return result

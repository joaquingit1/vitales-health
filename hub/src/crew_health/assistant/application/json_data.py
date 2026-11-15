import json
import math
from dataclasses import dataclass
from typing import cast

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.model.application.ports import JsonValue


@dataclass(frozen=True, slots=True)
class JsonBounds:
    max_bytes: int
    max_depth: int
    max_nodes: int
    max_array_items: int
    max_string_chars: int


def parse(raw: bytes, bounds: JsonBounds) -> JsonValue:
    if any(
        type(limit) is not int or limit < 1
        for limit in (
            bounds.max_bytes,
            bounds.max_depth,
            bounds.max_nodes,
            bounds.max_array_items,
            bounds.max_string_chars,
        )
    ):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "assistant JSON bounds invalid")
    if len(raw) > bounds.max_bytes:
        raise _invalid()
    try:
        value: object = json.loads(raw, object_pairs_hook=_unique)
    except ValueError, UnicodeError, RecursionError:
        raise _invalid() from None
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > bounds.max_nodes or depth > bounds.max_depth:
            raise _invalid()
        if isinstance(item, str):
            if len(item) > bounds.max_string_chars:
                raise _invalid()
        elif type(item) is int:
            if abs(item) > 9007199254740991:
                raise _invalid()
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise _invalid()
        elif isinstance(item, list):
            if len(item) > bounds.max_array_items:
                raise _invalid()
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if len(item) > bounds.max_array_items:
                raise _invalid()
            stack.extend((child, depth + 1) for pair in item.items() for child in pair)
        elif item is not None and type(item) is not bool:
            raise _invalid()
    return cast(JsonValue, value)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid() -> ProblemError:
    return ProblemError(ErrorCode.MODEL_OUTPUT_REJECTED, "assistant data invalid")

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from crew_health.tools.domain.errors import ToolsInvalid


def require_public_literal(query: str, *, max_chars: int, max_terms: int) -> None:
    if len(query) > max_chars:
        raise ToolsInvalid("PAYLOAD_TOO_LARGE")
    normalized = unicodedata.normalize("NFKC", query).casefold()
    words = normalized.split()
    if not words or len(normalized) > max_chars or len(words) > max_terms or ".." in normalized:
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    if any(not (char.isalnum() or char.isspace() or char in "'?,().-") for char in normalized):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    if any(
        word.strip("(),?.'")
        in {
            "select",
            "insert",
            "update",
            "delete",
            "drop",
            "alter",
            "create",
            "pragma",
            "attach",
            "detach",
            "exec",
            "execute",
            "union",
            "curl",
            "wget",
            "bash",
            "sh",
            "powershell",
            "cmd",
            "sudo",
            "python",
            "import",
        }
        for word in words
    ):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    if any(
        previous.isalnum() and current == "("
        for previous, current in zip(normalized, normalized[1:], strict=False)
    ):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")


@dataclass(frozen=True, slots=True)
class JsonBounds:
    max_bytes: int
    max_depth: int
    max_array_items: int
    max_string_chars: int

    def __post_init__(self) -> None:
        if any(
            type(n) is not int or n < 1
            for n in (self.max_bytes, self.max_depth, self.max_array_items, self.max_string_chars)
        ):
            raise ToolsInvalid("CONFIG_INVALID")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")
        result[key] = value
    return result


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or (number == 0 and Decimal(value) != 0):
        raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")
    return number


def _constant(value: str) -> None:
    raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")


def _depth(raw: str, maximum: int) -> None:
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > maximum:
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")


def _walk(value: object, bounds: JsonBounds) -> tuple[str, ...]:
    pending: list[object] = [value]
    texts: list[str] = []
    for _ in range(bounds.max_bytes + 1):
        if not pending:
            return tuple(texts)
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")
                pending.extend((key, child))
        elif isinstance(item, list):
            if len(item) > bounds.max_array_items:
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
            pending.extend(reversed(item))
        elif isinstance(item, str):
            if len(item) > bounds.max_string_chars:
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
            item.encode("utf-8", errors="strict")
            texts.append(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ToolsInvalid("MALFORMED_MODEL_OUTPUT")
    raise ToolsInvalid("PAYLOAD_TOO_LARGE")


def parse_json(raw: bytes, bounds: JsonBounds) -> object:
    if len(raw) > bounds.max_bytes:
        raise ToolsInvalid("PAYLOAD_TOO_LARGE")
    try:
        text = raw.decode("utf-8", errors="strict")
        _depth(text, bounds.max_depth)
        value: object = json.loads(
            text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_number
        )
        _walk(value, bounds)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ToolsInvalid("MALFORMED_MODEL_OUTPUT") from error
    return value


def text_fields(raw: bytes, bounds: JsonBounds) -> tuple[str, ...]:
    return _walk(parse_json(raw, bounds), bounds)

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from crew_health_bench.domain.errors import BenchError


def object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise BenchError("case_invalid")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def text(value: object) -> str:
    if not isinstance(value, str):
        raise BenchError("case_invalid")
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise BenchError("case_invalid")
    return value


def text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BenchError("case_invalid")
    return tuple(text(item) for item in value)


def number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise BenchError("case_invalid")
    result = Decimal(str(value))
    if not result.is_finite():
        raise BenchError("case_invalid")
    return result


def json_pointer(documents: Mapping[str, object], document: str, pointer: str) -> object:
    if document not in documents or (pointer and not pointer.startswith("/")):
        raise BenchError("config_reference_missing")
    current: object = documents[document]
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise BenchError("config_reference_missing")
    return current

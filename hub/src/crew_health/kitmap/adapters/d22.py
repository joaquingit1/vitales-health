"""Coordinator decision D22 until the contract change lands: condition IDs may carry one capital suffix.

The generated models still hold the old pattern `^ICL[1-9][0-9]{0,2}$`. Validation therefore runs on a copy in which
every suffixed ID (ICL36A, ICL36B) is replaced by an unused unsuffixed stand-in, so the rest of the shape is still
checked by the generated model; the stand-ins are mapped back in the validated output. The contract change is listed
as a request (D22).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

D22_CONDITION_ID = re.compile(r"^ICL[1-9][0-9]{0,2}[A-Z]?$")
CONTRACT_CONDITION_ID = re.compile(r"^ICL[1-9][0-9]{0,2}$")
ID_KEYS = frozenset({"condition_id"})
ID_LISTS = frozenset({"plans_to_write"})
MAX_STAND_INS = 999


def _ids(value: object, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ID_KEYS and isinstance(item, str):
                found.add(item)
            elif key in ID_LISTS and isinstance(item, list):
                found.update(i for i in item if isinstance(i, str))
            else:
                _ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _ids(item, found)


def stand_ins(document: object) -> dict[str, str]:
    """Suffixed ID to an unused contract-valid stand-in; empty when the document has no suffixed ID."""
    found: set[str] = set()
    _ids(document, found)
    suffixed = sorted(i for i in found if D22_CONDITION_ID.match(i) and not CONTRACT_CONDITION_ID.match(i))
    if not suffixed:
        return {}
    free = (f"ICL{n}" for n in range(MAX_STAND_INS, 0, -1) if f"ICL{n}" not in found)
    mapping = dict(zip(suffixed, free, strict=False))
    if len(mapping) != len(suffixed):
        raise ValueError("no free stand-in condition IDs")
    return mapping


def replace_ids(value: object, mapping: Mapping[str, str]) -> object:
    """A copy with every condition ID field (and plans_to_write item) mapped; other strings are left alone."""
    if not mapping:
        return value
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if key in ID_KEYS and isinstance(item, str):
                result[key] = mapping.get(item, item)
            elif key in ID_LISTS and isinstance(item, list):
                result[key] = [mapping.get(i, i) if isinstance(i, str) else i for i in item]
            else:
                result[key] = replace_ids(item, mapping)
        return result
    if isinstance(value, list):
        return [replace_ids(item, mapping) for item in value]
    return value

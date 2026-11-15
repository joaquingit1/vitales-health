from __future__ import annotations

import json
from datetime import datetime
from typing import cast

from crew_health.kernel.errors import ErrorCode, ProblemError


def instant(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def text(v: object) -> str:
    if not isinstance(v, str):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "invalid persisted string")
    return v


def integer(v: object) -> int:
    if type(v) is not int:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "invalid persisted integer")
    return v


def obj(v: object) -> dict[str, object]:
    if not isinstance(v, dict) or not all(isinstance(k, str) for k in v):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "invalid persisted object")
    return cast(dict[str, object], v)


def array(v: object) -> list[object]:
    if not isinstance(v, list):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "invalid persisted array")
    return cast(list[object], v)


def decode(raw: object) -> dict[str, object]:
    if not isinstance(raw, bytes):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "invalid persisted bytes")
    return obj(json.loads(raw))

from __future__ import annotations

import re
from typing import Final, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError

ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class IdFactory(Protocol):
    def new(self, prefix: str) -> str: ...
    def derived(self, prefix: str, *parts: str) -> str: ...


def require_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(ID_PATTERN, value) is None:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return value

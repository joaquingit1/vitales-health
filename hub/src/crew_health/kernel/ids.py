from __future__ import annotations

import re
from collections.abc import Callable
from hashlib import sha256
from typing import Final, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError

ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class IdFactory(Protocol):
    def new(self, prefix: str) -> str: ...
    def derived(self, prefix: str, *parts: str) -> str: ...


class KernelIdFactory:
    def __init__(self, *, new_uuid_hex: Callable[[], str]) -> None:
        self._new_uuid_hex = new_uuid_hex

    def new(self, prefix: str) -> str:
        return require_id(prefix + "_" + self._new_uuid_hex())

    def derived(self, prefix: str, *parts: str) -> str:
        from crew_health.kernel.commands import canonical_json

        try:
            digest = sha256(canonical_json([prefix, *parts])).hexdigest()
        except (ValueError, UnicodeError) as error:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
        return require_id(prefix + "_" + digest[:32])


def require_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(ID_PATTERN, value) is None:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return value

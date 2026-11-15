from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# ErrorCode is re-exported so pure domain code reaches it through the kernel (SYSTEM 6.4 domain-pure).
from crew_health.contracts_gen.enums import ERROR_RETRYABLE, ERROR_STATUS
from crew_health.contracts_gen.enums import ErrorCode as ErrorCode


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: str
    message: str


class ProblemError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        field_errors: Sequence[FieldError] = (),
        current_state: Mapping[str, object] | None = None,
        supported_alternative: Mapping[str, object] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = ERROR_STATUS[code]
        self.retryable = ERROR_RETRYABLE[code] if retryable is None else retryable
        self.field_errors = tuple(field_errors)
        self.current_state = dict(current_state) if current_state is not None else None
        self.supported_alternative = (
            dict(supported_alternative) if supported_alternative is not None else None
        )

    code: ErrorCode
    status: int
    retryable: bool


class StorageUnavailable(ProblemError):
    def __init__(self, cause: Literal["disk_full", "locked", "io_error", "corrupt"]) -> None:
        self.cause = cause
        super().__init__(ErrorCode.INTERNAL_ERROR, cause, retryable=True)

    cause: Literal["disk_full", "locked", "io_error", "corrupt"]  # internal enum


@dataclass(frozen=True, slots=True)
class Problem:
    type: str
    title: str
    status: int
    detail: str
    instance: str

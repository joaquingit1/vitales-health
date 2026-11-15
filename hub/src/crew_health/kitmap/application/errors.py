"""Typed kit map errors, mapped to the contract error envelope by the API layer (SYSTEM 5.5)."""

from __future__ import annotations

from collections.abc import Sequence

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError


class KitMapUnavailable(ProblemError):
    """No usable parse for the active release, or a required provider is absent: fail loudly, write nothing."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, reason, retryable=retryable)
        self.reason = reason


class InvalidEnvironment(ProblemError):
    def __init__(self, environment_id: str) -> None:
        super().__init__(
            ErrorCode.MALFORMED_REQUEST,
            ErrorCode.MALFORMED_REQUEST.value,
            field_errors=(FieldError("/environment_id", "enum", "unknown environment"),),
        )
        self.environment_id = environment_id


class WhatIfInvalid(ProblemError):
    def __init__(self, field_errors: Sequence[FieldError]) -> None:
        super().__init__(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value, field_errors=field_errors)


class KitGateInvalid(ProblemError):
    def __init__(self, field_errors: Sequence[FieldError]) -> None:
        super().__init__(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value, field_errors=field_errors)

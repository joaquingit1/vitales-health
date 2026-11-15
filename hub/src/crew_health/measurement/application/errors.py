"""Measurement failures on the wire (E05 6; SYSTEM 5.5): ProblemError subclasses mapped to the closed envelope,
and the single mapping from a domain refusal. Messages are codes only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.measurement.domain.errors import DomainRefusal


class MeasurementError(ProblemError):
    def __init__(
        self,
        code: ErrorCode,
        *,
        field: str | None = None,
        detail_code: str | None = None,
        current_state: Mapping[str, object] | None = None,
    ) -> None:
        errors: Sequence[FieldError] = ()
        if field is not None:
            errors = (FieldError(field, detail_code or code.value, detail_code or code.value),)
        super().__init__(code, code.value, field_errors=errors, current_state=current_state)


class InvalidInput(MeasurementError):
    def __init__(self, field: str, detail_code: str) -> None:
        super().__init__(ErrorCode.MALFORMED_REQUEST, field=field, detail_code=detail_code)


class InvalidValue(MeasurementError):
    def __init__(self, field: str, detail_code: str) -> None:
        super().__init__(ErrorCode.INVALID_VALUE, field=field, detail_code=detail_code)


class IdentityMismatch(MeasurementError):
    def __init__(self, field: str, detail_code: str) -> None:
        super().__init__(ErrorCode.IDENTITY_MISMATCH, field=field, detail_code=detail_code)


class StoredInvariant(ProblemError):
    """Stored or wire data that breaks an invariant: loud, not retryable, never a client error."""

    def __init__(self, name: str) -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, name, retryable=False)


def problem_of(refusal: DomainRefusal, *, current_state: Mapping[str, object] | None = None) -> ProblemError:
    if refusal.kind == "INTERNAL_ERROR":
        return StoredInvariant(refusal.detail_code)
    return MeasurementError(
        ErrorCode(refusal.kind),
        field=refusal.field,
        detail_code=refusal.detail_code if refusal.field is not None else None,
        current_state=current_state if current_state is not None else refusal.current_state,
    )

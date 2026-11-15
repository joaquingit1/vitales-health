"""Typed teach errors to the contract envelope codes (E30 4.7). field_errors name fields and reason codes, never a
value, count, answer or name."""

from __future__ import annotations

from collections.abc import Mapping

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.teach.domain.errors import TeachError

_CODES: Mapping[str, ErrorCode] = {
    "invalid_value": ErrorCode.INVALID_VALUE,
    "invalid_transition": ErrorCode.INVALID_TRANSITION,
    "context_inapplicable": ErrorCode.CONTEXT_INAPPLICABLE,
    "not_eligible": ErrorCode.ROLE_NOT_ELIGIBLE,
    "catalog_shape": ErrorCode.INTERNAL_ERROR,
}


def problem_of(error: TeachError, *, current_state: Mapping[str, object] | None = None) -> ProblemError:
    code = _CODES[error.kind]
    fields = [FieldError(field, error.reason, error.reason) for field in error.fields]
    if code is ErrorCode.INVALID_VALUE and not fields:
        fields = [FieldError("/", error.reason, error.reason)]
    state = current_state if code is ErrorCode.INVALID_TRANSITION else None
    return ProblemError(code, code.value, field_errors=fields, current_state=state)

"""Typed supplies errors to the contract error envelope (E32 6.1 table, D5)."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.supplies.application.context import MissionUnknown
from crew_health.supplies.domain.errors import (
    DocumentInvalid,
    FieldIssue,
    LedgerCorrupt,
    ReferenceNotVisible,
    ReleaseMismatch,
    SuppliesError,
    SupplyBelowZero,
    SupplyNotFound,
    UnitMismatch,
    UseInvalid,
    WhatIfInvalid,
)


def _fields(issues: tuple[FieldIssue, ...]) -> tuple[FieldError, ...]:
    return tuple(FieldError(i.field, i.code, i.message) for i in issues)


def to_problem(error: SuppliesError) -> ProblemError:
    if isinstance(error, SupplyNotFound):
        return ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
    if isinstance(error, UseInvalid | WhatIfInvalid):
        return ProblemError(ErrorCode.INVALID_VALUE, "invalid value", field_errors=_fields(error.issues))
    if isinstance(error, UnitMismatch):
        field = FieldError("/quantity", ErrorCode.INVALID_VALUE.value, "unit differs from the supply's unit")
        return ProblemError(ErrorCode.INVALID_VALUE, "unit mismatch", field_errors=(field,))
    if isinstance(error, SupplyBelowZero):
        field = FieldError(
            "/quantity", ErrorCode.SUPPLY_BELOW_ZERO.value, "the use would take stock below zero"
        )
        return ProblemError(ErrorCode.SUPPLY_BELOW_ZERO, "supply below zero", field_errors=(field,))
    if isinstance(error, ReleaseMismatch):
        return ProblemError(
            ErrorCode.CONFIG_VERSION_MISMATCH,
            ErrorCode.CONFIG_VERSION_MISMATCH.value,
            current_state={"config_release_id": error.active_release_id},
        )
    if isinstance(error, ReferenceNotVisible):
        return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    if isinstance(error, MissionUnknown):
        return ProblemError(ErrorCode.INTERNAL_ERROR, "no current mission", retryable=True)
    if isinstance(error, DocumentInvalid | LedgerCorrupt):
        return ProblemError(ErrorCode.INTERNAL_ERROR, "supplies unreadable", retryable=False)
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


def envelope(problem: ProblemError, correlation_id: str | None) -> JSONResponse:
    body: dict[str, object] = {
        "code": problem.code.value,
        "message": str(problem) or problem.code.value,
        "retryable": problem.retryable,
        "field_errors": [
            {"field": f.field, "code": f.code, "message": f.message} for f in problem.field_errors
        ],
        "correlation_id": correlation_id,
        "current_state": problem.current_state,
        "supported_alternative": problem.supported_alternative,
    }
    headers = {} if correlation_id is None else {"X-Correlation-Id": correlation_id}
    return JSONResponse(body, status_code=ERROR_STATUS[problem.code], headers=headers)


def malformed(message: str, field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST, message, field_errors=(FieldError(field, "malformed", message),)
    )

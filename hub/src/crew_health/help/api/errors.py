"""Typed help errors to the contract error envelope (D5, CONTRACTS 5). No bare exception leaves the router."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from crew_health.contracts_gen.enums import ERROR_RETRYABLE, ERROR_STATUS, ErrorCode
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.help.domain.errors import HelpError
from crew_health.kernel.errors import ProblemError


def envelope(
    code: ErrorCode,
    message: str,
    correlation_id: str,
    *,
    retryable: bool | None = None,
    field_errors: list[dict[str, str]] | None = None,
    current_state: dict[str, object] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "code": code.value,
        "message": message or code.value,
        "retryable": ERROR_RETRYABLE[code] if retryable is None else retryable,
        "field_errors": field_errors or [],
        "correlation_id": correlation_id,
        "current_state": current_state,
        "supported_alternative": None,
    }
    ErrorEnvelope.model_validate(body)
    return JSONResponse(body, status_code=ERROR_STATUS[code], headers={"X-Correlation-Id": correlation_id})


def from_help(error: HelpError, correlation_id: str) -> JSONResponse:
    code = ErrorCode(error.code)
    fields = []
    if error.field is not None:
        fields.append({"field": error.field, "code": error.field_code, "message": error.message})
    retryable = True if error.retryable else None
    return envelope(
        code,
        error.message,
        correlation_id,
        retryable=retryable,
        field_errors=fields,
        current_state=error.current_state,
    )


def from_problem(error: ProblemError, correlation_id: str) -> JSONResponse:
    fields = [{"field": f.field, "code": f.code, "message": f.message} for f in error.field_errors]
    return envelope(
        error.code,
        str(error),
        correlation_id,
        retryable=error.retryable,
        field_errors=fields,
        current_state=error.current_state,
    )


def internal(correlation_id: str) -> JSONResponse:
    """A storage or unexpected fault: nothing committed, the client keeps the command pending and retries."""
    return envelope(ErrorCode.INTERNAL_ERROR, "internal error", correlation_id, retryable=True)

"""Routine errors translated to the contract envelope without request content."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.routine.domain import errors

_SIMPLE = (
    (errors.NotFound, ErrorCode.NOT_FOUND),
    (errors.Unauthorized, ErrorCode.UNAUTHORIZED_SCOPE),
    (errors.ActionNotAllowed, ErrorCode.ACTION_NOT_ALLOWED),
    (errors.StaleVersion, ErrorCode.STALE_VERSION),
    (errors.InvalidTransition, ErrorCode.INVALID_TRANSITION),
    (errors.VersionRequired, ErrorCode.VERSION_REQUIRED),
    (errors.DuplicateOverride, ErrorCode.INVALID_TRANSITION),
    (errors.ConfigVersionMismatch, ErrorCode.CONFIG_VERSION_MISMATCH),
    (errors.ConfigInvalid, ErrorCode.CONFIG_INVALID),
    (errors.MissionUnavailable, ErrorCode.CAPABILITY_UNAVAILABLE),
)


class BoundaryMalformed(ProblemError):
    """A short, sanitized parse error created at this HTTP boundary."""


def malformed(field: str, code: str = "malformed") -> ProblemError:
    return BoundaryMalformed(
        ErrorCode.MALFORMED_REQUEST, "Malformed request", field_errors=(FieldError(field, code, code),)
    )


def to_problem(error: Exception, operation: str = "") -> ProblemError:
    if isinstance(error, BoundaryMalformed):
        return error
    if isinstance(error, ProblemError):
        # Providers may carry private diagnostic text. Only the code crosses HTTP.
        return ProblemError(error.code, error.code.value, retryable=error.retryable)
    if isinstance(error, errors.InvalidBody):
        code = ErrorCode.INVALID_VALUE if operation == "createOverride" else ErrorCode.MALFORMED_REQUEST
        return ProblemError(code, code.value, field_errors=(FieldError(error.pointer, "invalid", "invalid"),))
    for kind, code in _SIMPLE:
        if isinstance(error, kind):
            return ProblemError(code, code.value)
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


def envelope(problem: ProblemError, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "code": problem.code.value,
            "message": str(problem),
            "retryable": problem.retryable,
            "field_errors": [
                {"field": f.field, "code": f.code, "message": f.message} for f in problem.field_errors
            ],
            "correlation_id": correlation_id,
            "current_state": None,
            "supported_alternative": None,
        },
        status_code=ERROR_STATUS[problem.code],
    )

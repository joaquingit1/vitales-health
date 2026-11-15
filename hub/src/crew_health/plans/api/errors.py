"""Transport error envelopes use the package's closed schema and kernel status mapping."""

from fastapi.responses import JSONResponse

from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.plans.application.errors import problem as problem


def malformed(message: str, pointer: str) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST, message, field_errors=(FieldError(pointer, "malformed", message),)
    )


def envelope(error: ProblemError, correlation: str) -> JSONResponse:
    return JSONResponse(
        {
            "code": error.code.value,
            "message": str(error),
            "retryable": error.retryable,
            "field_errors": [
                {"field": f.field, "code": f.code, "message": f.message} for f in error.field_errors
            ],
            "correlation_id": correlation,
            "current_state": error.current_state,
            "supported_alternative": error.supported_alternative,
        },
        status_code=error.status,
    )

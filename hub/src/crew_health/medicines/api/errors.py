"""The contract error envelope (D5) for medicines routes."""

from __future__ import annotations

from fastapi.responses import Response

from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.medicines.application.codec import dumps


def envelope(problem: ProblemError, correlation_id: str) -> Response:
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
    return Response(
        dumps(body),
        status_code=ERROR_STATUS[problem.code],
        media_type="application/json",
        headers={"X-Correlation-Id": correlation_id},
    )


def malformed(message: str, field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST, message, field_errors=(FieldError(field, "malformed", message),)
    )

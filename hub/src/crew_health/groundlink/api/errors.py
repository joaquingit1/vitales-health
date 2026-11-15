"""D5: map typed failures to the shared safe problem envelope."""

from fastapi.responses import JSONResponse

from crew_health.app.errors import to_envelope
from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.groundlink.domain.errors import BundleConflict, DemoSwitchInvalid, GroundLinkError
from crew_health.kernel.errors import FieldError, ProblemError


def to_problem(error: GroundLinkError) -> ProblemError:
    if isinstance(error, DemoSwitchInvalid):
        return ProblemError(
            ErrorCode.INVALID_VALUE,
            ErrorCode.INVALID_VALUE.value,
            field_errors=(FieldError(error.field, "invalid", ErrorCode.INVALID_VALUE.value),),
        )
    if isinstance(error, BundleConflict):
        return ProblemError(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)


def envelope(problem: ProblemError, correlation_id: str) -> JSONResponse:
    body = to_envelope(problem, correlation_id).model_dump(mode="json")
    allowed = {"/status", "/gap_kind", "/gap_planned", "/next_contact_at", "/one_way_delay_s", "/text", "/"}
    body["field_errors"] = [
        {"field": f.field, "code": f.code, "message": problem.code.value}
        for f in problem.field_errors
        if f.field in allowed and f.code == "invalid"
    ]
    return JSONResponse(body, status_code=ERROR_STATUS[problem.code])

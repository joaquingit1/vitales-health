"""D5 and P29-2: unavailable or oversized results are retryable, never partial successes."""

from fastapi.responses import JSONResponse

from crew_health.app.errors import to_envelope
from crew_health.catchup.domain.errors import CatchUpError, ConsentRequired
from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.kernel.errors import ProblemError


def to_problem(error: CatchUpError) -> ProblemError:
    if isinstance(error, ConsentRequired):
        return ProblemError(ErrorCode.CONSENT_REQUIRED, ErrorCode.CONSENT_REQUIRED.value)
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)


def envelope(problem: ProblemError, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        to_envelope(problem, correlation_id).model_dump(mode="json"), status_code=ERROR_STATUS[problem.code]
    )

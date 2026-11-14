from crew_health.kernel.errors import (
    ErrorCode,
    ProblemError,
)


def fail(code: ErrorCode) -> ProblemError:
    return ProblemError(code, code.value)

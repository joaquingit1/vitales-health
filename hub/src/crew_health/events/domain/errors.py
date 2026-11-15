from crew_health.kernel.errors import ErrorCode, ProblemError


def failure(code: ErrorCode) -> ProblemError:
    return ProblemError(code, code.value)

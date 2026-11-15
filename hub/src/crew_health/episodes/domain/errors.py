from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError


def refused(code: ErrorCode, field: str = "/", reason: str = "refused") -> ProblemError:
    return ProblemError(code, code.value, field_errors=(FieldError(field, reason, reason),))

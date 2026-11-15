"""Shared typed failure without a dependency on the application composition."""

from crew_health.kernel.errors import ErrorCode, ProblemError


def internal() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, "plans_provider_unavailable", retryable=True)

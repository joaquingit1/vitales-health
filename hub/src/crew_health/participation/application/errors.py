"""Typed participation errors mapped to the contract envelope (E22 6). Messages name no person and no count."""

from __future__ import annotations

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.participation.domain.errors import (
    AggregateOnly,
    InvalidConsent,
    ParticipationError,
    UnauthorizedScope,
)


def unavailable() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, "participation is unavailable", retryable=True)


def no_mission() -> ProblemError:
    return ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "no mission roster is active")


def problem(error: ParticipationError) -> ProblemError:
    if isinstance(error, UnauthorizedScope):
        return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    if isinstance(error, AggregateOnly):
        return ProblemError(ErrorCode.AGGREGATE_ONLY, ErrorCode.AGGREGATE_ONLY.value)
    if isinstance(error, InvalidConsent):
        return ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
    return ProblemError(ErrorCode.INTERNAL_ERROR, "participation is unavailable", retryable=False)

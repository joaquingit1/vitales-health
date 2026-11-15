"""Plan domain failures mapped to the package error vocabulary."""

from collections.abc import Mapping

from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.plans.domain.errors import (
    InvalidField,
    InvalidTransition,
    PlansError,
    StaleVersion,
    StepNotAvailable,
    UnitMismatch,
)


def problem(error: PlansError, current: Mapping[str, object] | None = None) -> ProblemError:
    if isinstance(error, InvalidField):
        return ProblemError(
            ErrorCode.INVALID_VALUE,
            error.reason,
            field_errors=(FieldError(error.pointer, "invalid", error.reason),),
        )
    if isinstance(error, StepNotAvailable):
        return ProblemError(ErrorCode.STEP_NOT_AVAILABLE, "step_not_available", current_state=current)
    if isinstance(error, StaleVersion):
        return ProblemError(ErrorCode.STALE_VERSION, "stale_version", current_state=current)
    if isinstance(error, InvalidTransition):
        return ProblemError(ErrorCode.INVALID_TRANSITION, error.reason, current_state=current)
    if isinstance(error, UnitMismatch):
        return ProblemError(
            ErrorCode.INVALID_VALUE,
            "unit_mismatch",
            field_errors=(FieldError("/supplies", "unit_mismatch", error.supply_id),),
        )
    return ProblemError(ErrorCode.INTERNAL_ERROR, type(error).__name__, retryable=True)

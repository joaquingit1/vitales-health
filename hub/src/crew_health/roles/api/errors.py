"""Typed roles errors to the contract error envelope (E31 design 6.2, D5)."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi.responses import JSONResponse

from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.roles.domain.errors import (
    BadParticipants,
    ContextClosed,
    ContextNotFound,
    DrillNotFound,
    HumanOnly,
    InvalidDrill,
    KeyReused,
    NotAPerson,
    NotAssignee,
    NotCurrent,
    NotEligible,
    NotRunning,
    NotVisible,
    OriginMismatch,
    PortUnavailable,
    RevisionRequired,
    RoleNotFound,
    RolesError,
    RulesInvalid,
    StaleRevision,
    SuppliesNotAboard,
    TooManyOpenDrills,
)

_SIMPLE: tuple[tuple[type[RolesError], ErrorCode], ...] = (
    (ContextNotFound, ErrorCode.NOT_FOUND),
    (RoleNotFound, ErrorCode.NOT_FOUND),
    (DrillNotFound, ErrorCode.NOT_FOUND),
    (NotVisible, ErrorCode.UNAUTHORIZED_SCOPE),
    (NotAPerson, ErrorCode.UNAUTHORIZED_SCOPE),
    (NotAssignee, ErrorCode.UNAUTHORIZED_SCOPE),
    (HumanOnly, ErrorCode.UNAUTHORIZED_SCOPE),
    (RevisionRequired, ErrorCode.VERSION_REQUIRED),
    (KeyReused, ErrorCode.DUPLICATE_COMMAND),
    (OriginMismatch, ErrorCode.ORIGIN_MISMATCH),
    (TooManyOpenDrills, ErrorCode.CONTEXT_INAPPLICABLE),
    (RulesInvalid, ErrorCode.CONFIG_INVALID),
    (PortUnavailable, ErrorCode.INTERNAL_ERROR),
)


def to_problem(error: RolesError) -> ProblemError:
    if isinstance(error, StaleRevision):
        return ProblemError(ErrorCode.STALE_VERSION, "stale revision", current_state=error.current)
    if isinstance(error, NotCurrent):
        return ProblemError(
            ErrorCode.INVALID_TRANSITION, "assignment is not current", current_state=error.current
        )
    if isinstance(error, ContextClosed):
        code = ErrorCode.SESSION_CLOSED if error.kind == "help_session" else ErrorCode.INVALID_TRANSITION
        return ProblemError(code, "context closed", current_state=error.current)
    if isinstance(error, NotRunning):
        return ProblemError(ErrorCode.INVALID_TRANSITION, "drill not running", current_state=error.current)
    if isinstance(error, NotEligible):
        field = FieldError("/person_id", error.rule_id, error.text)
        return ProblemError(ErrorCode.ROLE_NOT_ELIGIBLE, "person not eligible", field_errors=(field,))
    if isinstance(error, InvalidDrill):
        field = FieldError(error.pointer, "invalid", error.message)
        return ProblemError(ErrorCode.INVALID_VALUE, error.message, field_errors=(field,))
    if isinstance(error, SuppliesNotAboard):
        fields = tuple(FieldError("/scenario_id", "not_aboard", s) for s in error.supply_ids)
        return ProblemError(ErrorCode.CONTEXT_INAPPLICABLE, "supplies not aboard", field_errors=fields)
    if isinstance(error, BadParticipants):
        field = FieldError("/participant_ids", "not_participants", "not a subset of the participants")
        return ProblemError(ErrorCode.MALFORMED_REQUEST, "bad participants", field_errors=(field,))
    for kind, code in _SIMPLE:
        if isinstance(error, kind):
            return ProblemError(code, code.value)
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


def envelope(problem: ProblemError, correlation_id: str | None) -> JSONResponse:
    body: Mapping[str, object] = {
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
    return JSONResponse(dict(body), status_code=ERROR_STATUS[problem.code])


def malformed(message: str, field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST, message, field_errors=(FieldError(field, "malformed", message),)
    )

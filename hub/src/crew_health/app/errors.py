from __future__ import annotations

from collections.abc import Mapping

from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.kernel.errors import Problem, ProblemError
from crew_health.kernel.ids import require_id


def _accepted(state: Mapping[str, object] | None) -> dict[str, object] | None:
    if (
        state is None
        or set(state) != {"kind", "command_key", "operation_id", "target", "receipt_id", "accepted_at"}
        or state["kind"] != "accepted_command"
    ):
        return None
    if not all(isinstance(value, str) for value in state.values()):
        return None
    for key in ("command_key", "receipt_id"):
        require_id(str(state[key]))
    return dict(state)


# CONTRACTS 5: these codes carry the current object as GET would return it. The module that raises them has already
# checked the session's right to read that object, so nothing else is filtered here.
CURRENT_STATE_CODES = frozenset({"STALE_VERSION", "INVALID_TRANSITION", "DUPLICATE_COMMAND"})


def _current_state(error: ProblemError) -> dict[str, object] | None:
    receipt = _accepted(error.current_state)
    if receipt is not None or error.current_state is None or error.code.value not in CURRENT_STATE_CODES:
        return receipt
    return dict(error.current_state)


def to_envelope(error: ProblemError, correlation_id: str) -> ErrorEnvelope:
    # Error messages, field text and alternatives may contain private user input.
    # The client renders its released configuration copy using the code.
    return ErrorEnvelope.model_validate(
        {
            "code": error.code.value,
            "message": error.code.value,
            "retryable": error.retryable,
            "field_errors": [
                {"field": f.field, "code": f.code, "message": f.message} for f in error.field_errors
            ],
            "correlation_id": require_id(correlation_id),
            "current_state": _current_state(error),
            "supported_alternative": None,
        }
    )


def to_problem(error: ProblemError, correlation_id: str, *, title: str) -> Problem:
    return Problem(
        "urn:crew-health:error:" + error.code.value,
        title,
        error.status,
        error.code.value,
        "urn:crew-health:correlation:" + require_id(correlation_id),
    )

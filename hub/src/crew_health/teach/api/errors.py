"""The contract error envelope (error.schema.json; E30 4.7). message is ui-copy error_messages[code] when the active
release has it, else the code. No envelope carries a beat count, value, answer or name."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.teach.application.content import decode
from crew_health.teach.application.errors import problem_of
from crew_health.teach.domain.errors import TeachError


def message_for(releases: ConfigReleaseReader | None, code: ErrorCode) -> str:
    if releases is None:
        return code.value
    try:
        copy = decode(releases.active().document(ConfigName.UI_COPY).raw)
    except ProblemError, ValueError:
        return code.value
    messages = copy.get("error_messages") if isinstance(copy, Mapping) else None
    text = messages.get(code.value) if isinstance(messages, Mapping) else None
    return text if isinstance(text, str) and text else code.value


def invalid_body(error: ValidationError) -> ProblemError:
    fields = []
    for item in error.errors()[:32]:
        pointer = "/" + "/".join(str(part) for part in item["loc"]) if item["loc"] else "/"
        fields.append(FieldError(pointer, str(item["type"]), str(item["type"])))
    return ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value, field_errors=fields)


def as_problem(error: ProblemError | TeachError) -> ProblemError:
    return problem_of(error) if isinstance(error, TeachError) else error


def envelope(error: ProblemError, correlation_id: str, releases: ConfigReleaseReader | None) -> JSONResponse:
    body = {
        "code": error.code.value,
        "message": message_for(releases, error.code),
        "retryable": error.retryable,
        "field_errors": [
            {"field": f.field, "code": f.code, "message": f.message} for f in error.field_errors
        ],
        "correlation_id": correlation_id,
        "current_state": error.current_state,
        "supported_alternative": error.supported_alternative,
    }
    return JSONResponse(body, status_code=error.status, headers={"X-Correlation-Id": correlation_id})

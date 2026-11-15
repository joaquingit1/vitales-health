"""HTTP mapping only. The authenticated facade owns grants, transactions and replay."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from crew_health.assistant.application.http_ports import (
    AssistantHttp,
    CommandRequest,
    HttpResult,
    QueryParameters,
)
from crew_health.assistant.application.ports import CommandFingerprint
from crew_health.contracts_gen.schemas.assessment_schema import Assessment
from crew_health.contracts_gen.schemas.assistant_message_schema import AssistantMessage
from crew_health.contracts_gen.schemas.assistant_status_schema import AssistantStatus
from crew_health.contracts_gen.schemas.common_schema import Page
from crew_health.contracts_gen.schemas.conversation_schema import Conversation
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.insight_schema import Insight
from crew_health.contracts_gen.schemas.job_schema import Job
from crew_health.contracts_gen.schemas.requests_schema import (
    AssessmentCreate,
    AssistantRuntimeAction,
    ConversationCreate,
    InsightAction,
    MessageCreate,
)
from crew_health.kernel.commands import canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.uow import SessionContext


@dataclass(slots=True)
class AssistantApi:
    facade: AssistantHttp | None
    ids: IdFactory | None
    fingerprint: CommandFingerprint | None
    max_body_bytes: int


class _InsightPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Insight]
    page: Page


@dataclass(frozen=True, slots=True)
class _Route:
    method: str
    path: str
    operation: str
    target: str | None
    status: int
    response: type[BaseModel] | None
    body: type[BaseModel] | None = None


_ROUTES = (
    _Route("GET", "/v1/assistant/status", "getAssistantStatus", None, 200, AssistantStatus),
    _Route(
        "POST",
        "/v1/assistant/runtime",
        "setAssistantRuntime",
        "assistant.runtime",
        200,
        AssistantStatus,
        AssistantRuntimeAction,
    ),
    _Route(
        "POST",
        "/v1/assistant/conversations",
        "openConversation",
        "assistant.conversations",
        201,
        Conversation,
        ConversationCreate,
    ),
    _Route(
        "GET",
        "/v1/assistant/conversations/{conversation_id}",
        "getConversation",
        "conversation_id",
        200,
        Conversation,
    ),
    _Route(
        "POST",
        "/v1/assistant/conversations/{conversation_id}/messages",
        "sendMessage",
        "conversation_id",
        202,
        AssistantMessage,
        MessageCreate,
    ),
    _Route(
        "GET",
        "/v1/assistant/conversations/{conversation_id}/stream",
        "streamConversation",
        "conversation_id",
        200,
        None,
    ),
    _Route(
        "POST",
        "/v1/assistant/conversations/{conversation_id}/cancel",
        "cancelTurn",
        "conversation_id",
        200,
        Conversation,
    ),
    _Route(
        "POST",
        "/v1/episodes/{episode_id}/assessments",
        "startAssessment",
        "episode_id",
        202,
        Job,
        AssessmentCreate,
    ),
    _Route("GET", "/v1/assessments/{assessment_id}", "getAssessment", "assessment_id", 200, Assessment),
    _Route("GET", "/v1/insights", "listInsights", None, 200, _InsightPage),
    _Route("GET", "/v1/insights/{insight_id}", "getInsight", "insight_id", 200, Insight),
    _Route(
        "POST", "/v1/insights/{insight_id}/actions", "actOnInsight", "insight_id", 200, Insight, InsightAction
    ),
)


def _problem(code: ErrorCode) -> ProblemError:
    return ProblemError(code, code.value)


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("nonfinite")


def _parse(raw: bytes) -> dict[str, object]:
    value: object = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("object required")
    canonical_json(value)  # Shared kernel depth, finite number and I-JSON ceilings.
    return cast(dict[str, object], value)


def _header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) > 1:
        raise _problem(ErrorCode.MALFORMED_REQUEST)
    return values[0] if values else None


def _revision(value: str | None) -> int | None:
    if value is None:
        return None
    if re.fullmatch(r'(?:[0-9]{1,15}|"[0-9]{1,15}")', value) is None:
        raise _problem(ErrorCode.MALFORMED_REQUEST)
    return int(value.strip('"'))


def _response(result: HttpResult, route: _Route) -> Response:
    if result.status != route.status or route.response is None:
        raise _problem(ErrorCode.INTERNAL_ERROR)
    try:
        route.response.model_validate(dict(result.body))
        raw = canonical_json(dict(result.body))
    except (ValueError, ValidationError, TypeError, RecursionError) as exc:
        raise _problem(ErrorCode.INTERNAL_ERROR) from exc
    return Response(
        raw, status_code=result.status, media_type="application/json", headers=dict(result.headers)
    )


def _error(error: ProblemError, correlation: str) -> Response:
    body = {
        "code": error.code.value,
        "message": str(error),
        "retryable": error.retryable,
        "field_errors": [
            {"field": f.field, "code": f.code, "message": f.message} for f in error.field_errors
        ],
        "current_state": error.current_state,
        "supported_alternative": error.supported_alternative,
        "correlation_id": correlation,
    }
    ErrorEnvelope.model_validate(body)
    return Response(
        canonical_json(body),
        status_code=error.status,
        media_type="application/json",
        headers={"X-Correlation-Id": correlation, "Cache-Control": "no-store"},
    )


async def _body(request: Request, maximum: int) -> bytes:
    # Core middleware supplies a validated canonical body; direct component hosts stay bounded.
    canonical: object = getattr(request.state, "canonical_body", None)
    if isinstance(canonical, bytes):
        if len(canonical) > maximum:
            raise _problem(ErrorCode.PAYLOAD_TOO_LARGE)
        return canonical
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > maximum:
            raise _problem(ErrorCode.PAYLOAD_TOO_LARGE)
        raw.extend(chunk)
    return bytes(raw)


def build_router(api: AssistantApi) -> APIRouter:
    if not 1 <= api.max_body_bytes <= 8388608:
        raise ValueError("invalid body bound")
    router = APIRouter()

    def endpoint(route: _Route) -> Callable[[Request], Awaitable[Response]]:
        async def handle(request: Request) -> Response:
            correlation = "corr_unavailable"
            dispatched = False
            try:
                incoming = _header(request, "X-Correlation-Id")
                correlation = (
                    require_id(incoming)
                    if incoming is not None
                    else (api.ids.new("corr") if api.ids else correlation)
                )
                session: object = getattr(request.state, "session", None)
                if not isinstance(session, SessionContext):
                    raise _problem(ErrorCode.UNAUTHENTICATED)
                facade = api.facade
                if facade is None:
                    raise _problem(ErrorCode.CAPABILITY_UNAVAILABLE)
                target = route.target
                if target in request.path_params:
                    target = require_id(str(request.path_params[target]))
                for key in set(request.query_params):
                    if key not in {"cursor", "limit"} or len(request.query_params.getlist(key)) != 1:
                        raise _problem(ErrorCode.MALFORMED_REQUEST)
                cursor = request.query_params.get("cursor")
                limit_text = request.query_params.get("limit")
                if limit_text is not None and re.fullmatch(r"[0-9]{1,15}", limit_text) is None:
                    raise _problem(ErrorCode.MALFORMED_REQUEST)
                parameters = QueryParameters(cursor, int(limit_text) if limit_text is not None else None)
                if route.operation == "streamConversation":
                    assert target is not None
                    dispatched = True
                    iterator = await run_in_threadpool(
                        facade.stream, session, target
                    )  # Facade authenticates before returning it.
                    return StreamingResponse(
                        iterator,
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-store",
                            "X-Accel-Buffering": "no",
                            "X-Correlation-Id": correlation,
                        },
                    )
                if route.method == "GET":
                    dispatched = True
                    result = await run_in_threadpool(
                        facade.query, session, route.operation, target, parameters
                    )
                else:
                    raw = await _body(request, api.max_body_bytes)
                    if route.body is None:
                        if raw or _header(request, "Content-Type"):
                            raise _problem(ErrorCode.MALFORMED_REQUEST)
                        body: dict[str, object] = {}
                    else:
                        if (_header(request, "Content-Type") or "").split(";", 1)[
                            0
                        ].strip().lower() != "application/json":
                            raise _problem(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
                        body = _parse(raw)
                        route.body.model_validate(body, strict=True)
                        if route.operation == "sendMessage" and (
                            (body.get("text") is None) == (body.get("reply_to_action") is None)
                        ):
                            raise _problem(ErrorCode.MALFORMED_REQUEST)
                    if api.fingerprint is None:
                        raise _problem(ErrorCode.CAPABILITY_UNAVAILABLE)
                    pin = _header(request, "Config-Release")
                    if pin is not None:
                        require_id(pin)
                    version = _revision(_header(request, "If-Match"))
                    identity = command_identity(
                        key=require_id(_header(request, "Idempotency-Key") or ""),
                        actor_ref=session.actor.id,
                        operation=route.operation,
                        target=target or "",
                        parameters_json=canonical_json(
                            {
                                "config_release_id": pin,
                                "expected_revision": version,
                                "query": dict(request.query_params),
                            }
                        ),
                        body_fingerprint=api.fingerprint.fingerprint(
                            canonical_json(body) if route.body else b""
                        ),
                        fingerprint_kind="hmac_sha256",
                    )
                    dispatched = True
                    result = await run_in_threadpool(
                        facade.command,
                        session,
                        route.operation,
                        target,
                        CommandRequest(identity, body, pin, version),
                    )
                response = _response(result, route)
                response.headers["X-Correlation-Id"] = correlation
                response.headers["Cache-Control"] = "no-store"
                return response
            except ProblemError as error:
                return _error(error, correlation)
            except ValueError, ValidationError, UnicodeError, RecursionError:
                return _error(
                    _problem(ErrorCode.INTERNAL_ERROR if dispatched else ErrorCode.MALFORMED_REQUEST),
                    correlation,
                )
            except Exception:
                return _error(_problem(ErrorCode.INTERNAL_ERROR), correlation)

        return handle

    for route in _ROUTES:
        router.add_api_route(
            route.path,
            endpoint(route),
            methods=[route.method],
            operation_id=route.operation,
            response_model=None,
        )
    return router

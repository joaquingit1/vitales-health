"""The ten Routine operations, with bounded parsing and fail-closed authorization."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.contracts_gen.schemas.requests_schema import OccurrenceAction, OverrideAction, OverrideCreate
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.routine.api.errors import envelope, malformed, to_problem
from crew_health.routine.application.ports import (
    OccurrenceActionBody,
    OverrideCreateBody,
    OverrideUntil,
    RoutineCommands,
    RoutineQueries,
)

type Authenticate = Callable[[Request, str], SessionContext]
RoutineQueriesApi = RoutineQueries
RoutineCommandsApi = RoutineCommands
_CREW = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER})
_AUTHORITY = frozenset({Role.CREW_MEDICAL_OFFICER, Role.GROUND})
_BODY_MAX = 16384  # Byte ceiling for a small command body, as in the roles HTTP boundary.


def _correlation(request: Request) -> str:
    value = request.headers.get("X-Correlation-Id", "")
    return (
        value
        if 1 <= len(value) <= 128 and value.replace("-", "").replace("_", "").isalnum()
        else "cor_" + uuid4().hex
    )


def _key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(value) <= 128:
        raise malformed("/Idempotency-Key")
    return value


def _if_match(request: Request) -> int | None:
    value = request.headers.get("If-Match")
    if value is None:
        return None
    match = re.fullmatch(r'(?:"([0-9]+)"|([0-9]+))', value.strip())
    if match is None or len(value) > 128:
        raise malformed("/If-Match")
    return int(match[1] or match[2])


def _page(request: Request) -> tuple[str | None, int]:
    raw = request.query_params.get("limit", "50")
    if re.fullmatch(r"[0-9]{1,3}", raw) is None or not 1 <= int(raw) <= 200:
        raise malformed("/limit")
    return request.query_params.get("cursor"), int(raw)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


async def _body(request: Request) -> dict[str, object]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ProblemError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, ErrorCode.UNSUPPORTED_MEDIA_TYPE.value)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _BODY_MAX:
            raise malformed("/", "body_too_large")
        raw.extend(chunk)
    try:
        value: object = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise malformed("/") from error
    if not isinstance(value, dict):
        raise malformed("/")
    return cast(dict[str, object], value)


def _nonfinite(value: str) -> object:
    raise ValueError("nonfinite JSON")


def _instant(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        at = value
        if at.tzinfo is None:
            raise ValueError("timezone required")
        return at.astimezone(UTC)
    except ValueError as error:
        raise malformed(field) from error


def _occurrence(body: dict[str, object]) -> OccurrenceActionBody:
    parsed = OccurrenceAction.model_validate(body)
    return OccurrenceActionBody(
        parsed.action,
        tuple(v.root for v in parsed.observation_ids or ()),
        tuple(v.root for v in parsed.answer_ids or ()),
        parsed.skip_reason,
        parsed.note,
        _instant(None if parsed.later_until is None else parsed.later_until.root, "/later_until"),
        _instant(None if parsed.completed_at is None else parsed.completed_at.root, "/completed_at"),
    )


def _override(body: dict[str, object]) -> OverrideCreateBody:
    parsed = OverrideCreate.model_validate(body)
    return OverrideCreateBody(
        parsed.item_id.root,
        parsed.trigger_index,
        None if parsed.person_id is None else parsed.person_id.root,
        OverrideUntil(parsed.until.kind, parsed.until.days),
        parsed.note,
    )


def build_router(
    queries: RoutineQueriesApi, commands: RoutineCommandsApi, authenticate: Authenticate
) -> APIRouter:
    router = APIRouter()

    def session(request: Request, operation: str) -> SessionContext:
        result = authenticate(request, operation)
        roles = (
            _AUTHORITY
            if operation in ("createOverride", "actOnOverride")
            else _CREW | {Role.GROUND}
            if operation == "listOverrides"
            else _CREW
        )
        if not result.roles & roles:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        return result

    def response(request: Request, status: int, body: dict[str, object]) -> Response:
        headers = {
            "X-Correlation-Id": str(request.state.routine_correlation),
            "Config-Release": queries.active_release_id(),
        }
        if isinstance(body.get("revision"), int):
            headers["ETag"] = f'"{body["revision"]}"'
        if status == 201:
            headers["Location"] = f"/v1/routine/overrides/{body['override_id']}"
        return JSONResponse(body, status_code=status, headers=headers)

    def failure(request: Request, error: Exception, operation: str) -> Response:
        if isinstance(error, ValidationError):
            loc = error.errors(include_input=False)[0]["loc"]
            pointer = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in loc)
            problem = malformed(pointer)
        else:
            problem = to_problem(error, operation)
        result = envelope(problem, str(request.state.routine_correlation))
        result.headers["X-Correlation-Id"] = str(request.state.routine_correlation)
        try:
            result.headers["Config-Release"] = queries.active_release_id()
        except Exception:
            pass  # No active release is knowable before binding.
        return result

    def get(request: Request, operation: str, run: Callable[[SessionContext], dict[str, object]]) -> Response:
        request.state.routine_correlation = _correlation(request)
        try:
            return response(request, 200, run(session(request, operation)))
        except Exception as error:
            return failure(request, error, operation)

    @router.get("/v1/routine/today", operation_id="getRoutineToday")
    def today(request: Request) -> Response:
        return get(request, "getRoutineToday", lambda s: queries.schedule(s, "today"))

    @router.get("/v1/routine/week", operation_id="getRoutineWeek")
    def week(request: Request) -> Response:
        return get(request, "getRoutineWeek", lambda s: queries.schedule(s, "week"))

    @router.get("/v1/routine/occurrences", operation_id="listOccurrences")
    def occurrences(request: Request) -> Response:
        return get(
            request,
            "listOccurrences",
            lambda s: queries.occurrences(
                s,
                request.query_params.get("range", "today"),
                request.query_params.get("state"),
                *_page(request),
            ),
        )

    @router.get("/v1/routine/occurrences/{occurrence_id}", operation_id="getOccurrence")
    def occurrence(occurrence_id: str, request: Request) -> Response:
        return get(request, "getOccurrence", lambda s: queries.occurrence(s, occurrence_id))

    @router.get("/v1/routine/tasks", operation_id="listRoutineTasks")
    def tasks(request: Request) -> Response:
        return get(request, "listRoutineTasks", lambda s: queries.tasks(s, *_page(request)))

    @router.get("/v1/routine/tasks/{item_id}", operation_id="getRoutineTask")
    def task(item_id: str, request: Request) -> Response:
        return get(request, "getRoutineTask", lambda s: queries.task(s, item_id))

    @router.get("/v1/routine/overrides", operation_id="listOverrides")
    def overrides(request: Request) -> Response:
        return get(request, "listOverrides", lambda s: queries.overrides(s, *_page(request)))

    async def post(request: Request, operation: str, target: str) -> Response:
        request.state.routine_correlation = _correlation(request)
        try:
            caller = session(request, operation)
            key, expected = _key(request), _if_match(request)
            body = await _body(request)
            release = request.headers.get("Config-Release")
            if operation == "actOnOccurrence":
                status, out = commands.act(
                    caller,
                    target,
                    _occurrence(body),
                    if_match=expected,
                    idempotency_key=key,
                    config_release=release,
                )
            elif operation == "createOverride":
                status, out = commands.create_override(
                    caller, _override(body), idempotency_key=key, config_release=release
                )
            else:
                parsed = OverrideAction.model_validate(body)
                status, out = commands.end_override(
                    caller,
                    target,
                    if_match=expected,
                    idempotency_key=key,
                    config_release=release,
                    note=parsed.note,
                )
            return response(request, status, out)
        except Exception as error:
            return failure(request, error, operation)

    @router.post("/v1/routine/occurrences/{occurrence_id}/actions", operation_id="actOnOccurrence")
    async def act(occurrence_id: str, request: Request) -> Response:
        return await post(request, "actOnOccurrence", occurrence_id)

    @router.post("/v1/routine/overrides", operation_id="createOverride")
    async def create(request: Request) -> Response:
        return await post(request, "createOverride", "")

    @router.post("/v1/routine/overrides/{override_id}/actions", operation_id="actOnOverride")
    async def end(override_id: str, request: Request) -> Response:
        return await post(request, "actOnOverride", override_id)

    return router

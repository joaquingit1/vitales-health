"""The six OpenAPI plan operations; public content and clinical records have separate grants."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health.contracts_gen.enums import Role
from crew_health.contracts_gen.schemas.common_schema import Id
from crew_health.contracts_gen.schemas.requests_schema import PlanRunAction, PlanRunCreate
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.plans.api.errors import envelope, malformed, problem
from crew_health.plans.application.act import ActOnPlanRun
from crew_health.plans.application.runs import PlanRuns
from crew_health.plans.application.views import PlanViews
from crew_health.plans.application.world import PlansWorld, fail
from crew_health.plans.domain.errors import PlansError


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RequestAuthenticator(Protocol):
    def session(self, request: Request, operation_id: str) -> SessionContext: ...


class Runtime(Protocol):
    def need(self) -> PlansWorld: ...
    def authenticate(self, request: Request, operation: str) -> SessionContext: ...


def correlation(request: Request) -> str:
    value = request.headers.get("X-Correlation-Id", "cor-plans")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else "cor-plans"


def key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(value) <= 128:
        raise malformed("Idempotency-Key missing or malformed", "/Idempotency-Key")
    return value


def revision(request: Request) -> int | None:
    value = request.headers.get("If-Match")
    if value is None:
        return None
    if not re.fullmatch(r'(?:[0-9]{1,19}|"[0-9]{1,19}")', value):
        raise malformed("If-Match is not a revision", "/If-Match")
    return int(value.strip('"'))


async def body(request: Request, maximum: int) -> dict[str, object]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise fail(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > maximum:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise malformed("body is not JSON", "/") from error
    if not isinstance(value, dict):
        raise malformed("body is not an object", "/")
    return value


def path_parameters(request: Request) -> None:
    for name, value in request.path_params.items():
        if name == "condition_id":
            # D22 accepts the letter suffix while the generated condition model awaits regeneration.
            valid = re.fullmatch(r"ICL[1-9][0-9]{0,2}[A-Z]?", value) is not None
        elif name == "plan_id":
            valid = re.fullmatch(r"plan_[a-z0-9_]{1,59}", value) is not None
        else:
            try:
                Id.model_validate(value)
                valid = True
            except ValidationError:
                valid = False
        if not valid:
            raise malformed("invalid path parameter", "/" + name)


def build_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    def reply(request: Request, run: Callable[[], tuple[int, dict[str, object]]]) -> Response:
        try:
            status, result = run()
            response: Response = JSONResponse(result, status_code=status)
            if isinstance(result.get("revision"), int):
                response.headers["ETag"] = '"' + str(result["revision"]) + '"'
            if status == 201:
                response.headers["Location"] = "/v1/plan-runs/" + str(result["plan_run_id"])
        except PlansError as error:
            response = envelope(problem(error), correlation(request))
        except ProblemError as error:
            response = envelope(error, correlation(request))
        except ValidationError as error:
            first = error.errors()[0]
            pointer = "/" + "/".join(str(p) for p in first["loc"])
            response = envelope(malformed("invalid request", pointer), correlation(request))
        response.headers["X-Correlation-Id"] = correlation(request)
        with runtime.need().snapshots.snapshot() as snapshot:
            response.headers["Config-Release"] = snapshot.release.release_id
        return response

    def session(request: Request, operation: str) -> SessionContext:
        caller = runtime.authenticate(request, operation)
        content = operation in ("getPlan", "getConditionPlan")
        roles = {Role.CREW, Role.CREW_MEDICAL_OFFICER, Role.STATION}
        if operation == "getPlanRun":
            roles.add(Role.GROUND)
        if content:
            roles.update((Role.GROUND, Role.OPERATOR, Role.ADAPTER, Role.EDITOR))
        if caller.actor.kind == "assistant" or not caller.roles & roles:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        path_parameters(request)
        return caller

    @router.get("/v1/conditions/{condition_id}/plan", operation_id="getConditionPlan")
    def condition(condition_id: str, request: Request, environment_id: str | None = None) -> Response:
        return reply(
            request,
            lambda: (
                200,
                PlanViews(runtime.need()).condition_plan(
                    session(request, "getConditionPlan"), condition_id, environment_id
                ),
            ),
        )

    @router.get("/v1/plans/{plan_id}", operation_id="getPlan")
    def plan(plan_id: str, request: Request, environment_id: str | None = None) -> Response:
        return reply(
            request,
            lambda: (
                200,
                PlanViews(runtime.need()).plan(session(request, "getPlan"), plan_id, environment_id),
            ),
        )

    @router.get("/v1/plan-runs", operation_id="listPlanRuns")
    def runs(
        request: Request, subject: str | None = None, cursor: str | None = None, limit: str = "50"
    ) -> Response:
        def run() -> tuple[int, dict[str, object]]:
            caller = session(request, "listPlanRuns")
            if not re.fullmatch(r"[0-9]{1,3}", limit):
                raise malformed("invalid limit", "/limit")
            if cursor is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", cursor):
                raise malformed("invalid cursor", "/cursor")
            return 200, PlanRuns(runtime.need()).list(caller, subject, cursor, int(limit))

        return reply(request, run)

    @router.get("/v1/plan-runs/{plan_run_id}", operation_id="getPlanRun")
    def get_run(plan_run_id: str, request: Request) -> Response:
        return reply(
            request, lambda: (200, PlanRuns(runtime.need()).get(session(request, "getPlanRun"), plan_run_id))
        )

    async def command(request: Request, run_id: str | None) -> Response:
        operation = "startPlanRun" if run_id is None else "actOnPlanRun"
        try:
            caller = session(request, operation)
            command_key = key(request)
            parsed = await body(request, runtime.need().settings.max_document_bytes)
        except ProblemError as caught:

            def rejected(error: ProblemError = caught) -> tuple[int, dict[str, object]]:
                raise error

            return reply(request, rejected)

        def run() -> tuple[int, dict[str, object]]:
            config = request.headers.get("Config-Release")
            if run_id is None:
                return PlanRuns(runtime.need()).start(
                    caller, PlanRunCreate.model_validate(parsed), command_key, config
                )
            return 200, ActOnPlanRun(runtime.need()).act(
                caller, run_id, PlanRunAction.model_validate(parsed), command_key, revision(request), config
            )

        return reply(request, run)

    @router.post("/v1/plan-runs", operation_id="startPlanRun")
    async def start(request: Request) -> Response:
        return await command(request, None)

    @router.post("/v1/plan-runs/{plan_run_id}/actions", operation_id="actOnPlanRun")
    async def act(plan_run_id: str, request: Request) -> Response:
        return await command(request, plan_run_id)

    return router

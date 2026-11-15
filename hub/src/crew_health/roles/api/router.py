"""FastAPI routes of the roles module (E31 design 6.1); bodies parsed with the generated contract models."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.contracts_gen.schemas.requests_schema import DrillComplete, DrillCreate, RoleAction
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.roles.api.errors import envelope, malformed, to_problem
from crew_health.roles.application.actions import RoleCommand
from crew_health.roles.application.skills import DrillFinish, DrillStart
from crew_health.roles.domain.engine import ContextKind
from crew_health.roles.domain.errors import RolesError


# Provided by E02 (crew_health.identity); structural copy until it is on main. Resolves the caller's session for one
# operation or raises the contract's UNAUTHENTICATED problem.
class RequestAuthenticator(Protocol):
    def session(self, request: Request, operation_id: str) -> SessionContext: ...


type Authenticate = Callable[[Request, str], SessionContext]


class RoleActionsApi(Protocol):
    """What the routes call on crew_health.roles.application.actions.RoleActions."""

    def act(self, session: SessionContext, cmd: RoleCommand) -> tuple[int, bytes]: ...
    def help_roles(self, session: SessionContext, help_session_id: str) -> dict[str, object]: ...
    def plan_run_roles(
        self, session: SessionContext, plan_run_id: str, offset: int, limit: int
    ) -> dict[str, object]: ...


class SkillsApi(Protocol):
    """What the routes call on crew_health.roles.application.skills.Skills."""

    def my_skills(self, session: SessionContext) -> dict[str, object]: ...
    def readiness(self, session: SessionContext) -> dict[str, object]: ...
    def start_drill(self, session: SessionContext, cmd: DrillStart) -> tuple[int, bytes]: ...
    def get_drill(self, session: SessionContext, drill_id: str) -> dict[str, object]: ...
    def complete_drill(self, session: SessionContext, cmd: DrillFinish) -> tuple[int, bytes]: ...


CREW_ROLES = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER, Role.STATION})
_KEY_MIN, _KEY_MAX = 8, 128
_BODY_MAX = 16384  # bytes; a role action or a drill body is a few hundred


def _correlation(request: Request) -> str:
    value = request.headers.get("X-Correlation-Id")
    return (
        value
        if value and 1 <= len(value) <= 128 and value.replace("-", "").replace("_", "").isalnum()
        else "cor-roles"
    )


def _key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if key is None or not _KEY_MIN <= len(key) <= _KEY_MAX:
        raise malformed("Idempotency-Key missing or malformed", "/Idempotency-Key")
    return key


def _if_match(request: Request) -> int | None:
    raw = request.headers.get("If-Match")
    if raw is None:
        return None
    text = raw.strip().removeprefix("W/").strip('"')
    if not text.isdigit():
        raise malformed("If-Match is not a revision", "/If-Match")
    return int(text)


async def _body(request: Request) -> dict[str, object]:
    raw = await request.body()
    if len(raw) > _BODY_MAX:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise malformed("body is not JSON", "/") from error
    if not isinstance(value, dict):
        raise malformed("body is not an object", "/")
    return value


def _session(
    authenticate: Authenticate, request: Request, operation: str, roles: frozenset[Role]
) -> SessionContext:
    session = authenticate(request, operation)
    if not session.roles & roles:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return session


def _json(status: int, body: bytes, revision: int | None = None) -> Response:
    headers = {} if revision is None else {"ETag": f'"{revision}"'}
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def _revision(body: bytes) -> int | None:
    value = json.loads(body).get("revision")
    return value if isinstance(value, int) else None


def build_router(actions: RoleActionsApi, skills: SkillsApi, authenticate: Authenticate) -> APIRouter:
    router = APIRouter()

    def guarded(request: Request, run: Callable[[], Response]) -> Response:
        try:
            return run()
        except RolesError as error:
            return envelope(to_problem(error), _correlation(request))
        except ProblemError as problem:
            return envelope(problem, _correlation(request))

    async def act(request: Request, kind: ContextKind, context_id: str, operation: str) -> Response:
        try:
            session = _session(authenticate, request, operation, CREW_ROLES)
            key, if_match, body = _key(request), _if_match(request), await _body(request)
            parsed = RoleAction.model_validate(body)
        except ValidationError as error:
            return envelope(malformed(_first(error), "/"), _correlation(request))
        except ProblemError as problem:
            return envelope(problem, _correlation(request))
        if parsed.action == "reassign" and parsed.person_id is None:
            return envelope(
                malformed("person_id is required to reassign", "/person_id"), _correlation(request)
            )
        cmd = RoleCommand(
            kind,
            context_id,
            parsed.action,
            parsed.role_assignment_id.root,
            None if parsed.person_id is None else parsed.person_id.root,
            if_match,
            key,
            body,
            request.headers.get("Config-Release"),
        )

        def run() -> Response:
            status, out = actions.act(session, cmd)
            return _json(status, out, _revision(out))

        return guarded(request, run)

    @router.get("/v1/help-sessions/{help_session_id}/roles", operation_id="getHelpRoles")
    def get_help_roles(help_session_id: str, request: Request) -> Response:
        def run() -> Response:
            session = _session(authenticate, request, "getHelpRoles", CREW_ROLES)
            return JSONResponse(actions.help_roles(session, help_session_id))

        return guarded(request, run)

    @router.post("/v1/help-sessions/{help_session_id}/roles", operation_id="actOnHelpRole")
    async def act_on_help_role(help_session_id: str, request: Request) -> Response:
        return await act(request, "help_session", help_session_id, "actOnHelpRole")

    @router.get("/v1/plan-runs/{plan_run_id}/roles", operation_id="listPlanRunRoles")
    def list_plan_run_roles(
        plan_run_id: str, request: Request, cursor: str | None = None, limit: int = 50
    ) -> Response:
        def run() -> Response:
            session = _session(authenticate, request, "listPlanRunRoles", CREW_ROLES)
            if cursor is not None and not cursor.isdigit():
                raise malformed("cursor", "/cursor")
            offset = 0 if cursor is None else int(cursor)
            return JSONResponse(actions.plan_run_roles(session, plan_run_id, offset, limit))

        return guarded(request, run)

    @router.post("/v1/plan-runs/{plan_run_id}/roles", operation_id="actOnPlanRunRole")
    async def act_on_plan_run_role(plan_run_id: str, request: Request) -> Response:
        return await act(request, "plan_run", plan_run_id, "actOnPlanRunRole")

    @router.get("/v1/me/skills", operation_id="getMySkills")
    def get_my_skills(request: Request) -> Response:
        def run() -> Response:
            session = _session(
                authenticate, request, "getMySkills", frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER})
            )
            return JSONResponse(skills.my_skills(session))

        return guarded(request, run)

    @router.get("/v1/crew/readiness", operation_id="getCrewReadiness")
    def get_crew_readiness(request: Request) -> Response:
        def run() -> Response:
            session = _session(
                authenticate, request, "getCrewReadiness", frozenset({Role.CREW_MEDICAL_OFFICER})
            )
            return JSONResponse(skills.readiness(session))

        return guarded(request, run)

    @router.post("/v1/drills", operation_id="startDrill")
    async def start_drill(request: Request) -> Response:
        try:
            session = _session(authenticate, request, "startDrill", CREW_ROLES)
            key, body = _key(request), await _body(request)
            parsed = DrillCreate.model_validate(body)
        except ValidationError as error:
            return envelope(malformed(_first(error), "/"), _correlation(request))
        except ProblemError as problem:
            return envelope(problem, _correlation(request))
        cmd = DrillStart(
            parsed.scenario_id.root,
            tuple(s.root for s in parsed.skill_ids),
            tuple(p.root for p in parsed.participant_ids),
            None if parsed.routine_occurrence_id is None else parsed.routine_occurrence_id.root,
            parsed.origin_mode,
            key,
            body,
        )

        def run() -> Response:
            status, out = skills.start_drill(session, cmd)
            response = _json(status, out, _revision(out))
            response.headers["Location"] = "/v1/drills/" + str(json.loads(out)["drill_id"])
            return response

        return guarded(request, run)

    @router.get("/v1/drills/{drill_id}", operation_id="getDrill")
    def get_drill(drill_id: str, request: Request) -> Response:
        def run() -> Response:
            session = _session(authenticate, request, "getDrill", CREW_ROLES)
            return JSONResponse(skills.get_drill(session, drill_id))

        return guarded(request, run)

    @router.post("/v1/drills/{drill_id}/complete", operation_id="completeDrill")
    async def complete_drill(drill_id: str, request: Request) -> Response:
        try:
            session = _session(authenticate, request, "completeDrill", CREW_ROLES)
            key, if_match, body = _key(request), _if_match(request), await _body(request)
            parsed = DrillComplete.model_validate(body)
        except ValidationError as error:
            return envelope(malformed(_first(error), "/"), _correlation(request))
        except ProblemError as problem:
            return envelope(problem, _correlation(request))
        chosen = None if parsed.participant_ids is None else tuple(p.root for p in parsed.participant_ids)
        cmd = DrillFinish(drill_id, chosen, if_match, key, body)

        def run() -> Response:
            status, out = skills.complete_drill(session, cmd)
            return _json(status, out, _revision(out))

        return guarded(request, run)

    return router


def _first(error: ValidationError) -> str:
    errors = error.errors()
    if not errors:
        return "invalid body"
    location = "/".join(str(p) for p in errors[0]["loc"])
    return f"invalid {location or 'body'}"

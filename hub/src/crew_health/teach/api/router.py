"""HTTP routes of the teach module (E30 section 6): startGuidedCheck, getGuidedCheck, tapBeat, actOnGuidedCheck and
listCoachingSamples, plus GET /v1/guided-checks/{id}/steps, the pinned rendered steps (X1 interim, not in the
contract yet). Bodies are parsed with the generated models; every failure is the contract error envelope.

The router is built at registration with a late-bound holder; composition fills it when the ports resolve."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.requests_schema import (
    GuidedCheckAction,
    GuidedCheckBeat,
    GuidedCheckCreate,
)
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN, IdFactory
from crew_health.kernel.uow import SessionContext
from crew_health.teach.api.errors import as_problem, envelope, invalid_body
from crew_health.teach.application.actions import ActionInput, ActOnGuidedCheck
from crew_health.teach.application.beats import BeatInput, TapBeat
from crew_health.teach.application.commands import CommandMeta, TeachCore, TeachPorts
from crew_health.teach.application.commands import Response as Result
from crew_health.teach.application.finish import FinishGuidedCheck
from crew_health.teach.application.ports import (
    CoachingSampleRepository,
    HelpSessionReader,
    RequestAuthenticator,
)
from crew_health.teach.application.reads import CoachingSamples, GetGuidedCheck
from crew_health.teach.application.start import StartGuidedCheck, StartInput
from crew_health.teach.domain.errors import TeachError

MAX_BODY_BYTES = 16384  # every teach request body is a few fields; larger bodies are refused before parsing


@dataclass(frozen=True, slots=True)
class TeachServices:
    authenticator: RequestAuthenticator
    ids: IdFactory
    releases: ConfigReleaseReader
    start: StartGuidedCheck
    beat: TapBeat
    act: ActOnGuidedCheck
    finish: FinishGuidedCheck
    get: GetGuidedCheck
    coaching: CoachingSamples


class TeachApi:
    """Late-bound holder of the services; a request before binding is a 500, never a partial answer."""

    def __init__(self) -> None:
        self.services: TeachServices | None = None

    def bound(self) -> TeachServices:
        if self.services is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        return self.services


@dataclass(frozen=True, slots=True)
class _Call:
    session: SessionContext
    correlation_id: str
    body: bytes
    headers: dict[str, str]

    def meta(self, key_required: bool) -> CommandMeta:
        key = self.headers.get("idempotency-key")
        if key is None and key_required:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        return CommandMeta(self.session, key or "read", self.correlation_id, self.body)


def _parse[M: BaseModel](model: type[M], raw: bytes) -> tuple[M, bytes]:
    if len(raw) > MAX_BODY_BYTES:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    try:
        parsed = model.model_validate_json(raw)
    except ValidationError as error:
        try:
            json.loads(raw)
        except ValueError:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
        raise invalid_body(error) from error
    return parsed, canonical_json(parsed.model_dump(mode="json"))


def _json(result: Result, correlation_id: str, extra: dict[str, str] | None = None) -> JSONResponse:
    headers = {
        "ETag": f'"{result.revision}"',
        "Config-Release": result.release_id,
        "X-Correlation-Id": correlation_id,
        **(extra or {}),
    }
    return JSONResponse(result.body, status_code=result.status, headers=headers)


def build_router(api: TeachApi) -> APIRouter:
    router = APIRouter()

    async def run(
        request: Request, operation: str, work: Callable[[_Call, TeachServices], Response]
    ) -> Response:
        header = request.headers.get("x-correlation-id")
        correlation = header if header is not None and re.fullmatch(ID_PATTERN, header) else None
        services: TeachServices | None = api.services
        try:
            services = api.bound()
            correlation = correlation or services.ids.new("cor")
            headers = {k.lower(): v for k, v in request.headers.items()}
            session = services.authenticator.authenticate(headers, operation)
            call = _Call(session, correlation, await request.body(), headers)
            bound = services
            return await run_in_threadpool(lambda: work(call, bound))
        except (ProblemError, TeachError) as error:
            releases = services.releases if services is not None else None
            return envelope(as_problem(error), correlation or "unknown", releases)

    @router.post("/v1/guided-checks", operation_id="startGuidedCheck")
    async def start_guided_check(request: Request) -> Response:
        def work(call: _Call, s: TeachServices) -> Response:
            body, canonical = _parse(GuidedCheckCreate, call.body)
            meta = _Call(call.session, call.correlation_id, canonical, call.headers).meta(True)
            data = StartInput(
                skill_id=body.skill_id.root,
                helper_person_id=body.helper_person_id.root,
                subject_person_id=body.subject_person_id.root,
                plan_run_id=None if body.plan_run_id is None else body.plan_run_id.root,
                drill_id=None if body.drill_id is None else body.drill_id.root,
                role_assignment_id=None if body.role_assignment_id is None else body.role_assignment_id.root,
                config_release=call.headers.get("config-release"),
            )
            result = s.start(meta, data)
            location = f"/v1/guided-checks/{result.body['guided_check_id']}"
            return _json(result, call.correlation_id, {"Location": location})

        return await run(request, "startGuidedCheck", work)

    @router.get("/v1/guided-checks/{guided_check_id}", operation_id="getGuidedCheck")
    async def get_guided_check(guided_check_id: str, request: Request) -> Response:
        def work(call: _Call, s: TeachServices) -> Response:
            return _json(s.get(call.meta(False), guided_check_id), call.correlation_id)

        return await run(request, "getGuidedCheck", work)

    @router.get("/v1/guided-checks/{guided_check_id}/steps", operation_id="getGuidedCheckSteps")
    async def get_guided_check_steps(guided_check_id: str, request: Request) -> Response:
        def work(call: _Call, s: TeachServices) -> Response:
            body = s.get.rendered(call.meta(False), guided_check_id)
            return JSONResponse(body, headers={"X-Correlation-Id": call.correlation_id})

        return await run(request, "getGuidedCheck", work)

    @router.post("/v1/guided-checks/{guided_check_id}/beats", operation_id="tapBeat")
    async def tap_beat(guided_check_id: str, request: Request) -> Response:
        def work(call: _Call, s: TeachServices) -> Response:
            body, canonical = _parse(GuidedCheckBeat, call.body)
            meta = _Call(call.session, call.correlation_id, canonical, call.headers).meta(True)
            result = s.beat(meta, guided_check_id, BeatInput(body.sequence, body.client_time.root))
            return _json(result, call.correlation_id)

        return await run(request, "tapBeat", work)

    @router.post("/v1/guided-checks/{guided_check_id}/actions", operation_id="actOnGuidedCheck")
    async def act_on_guided_check(guided_check_id: str, request: Request) -> Response:
        def work(call: _Call, s: TeachServices) -> Response:
            body, canonical = _parse(GuidedCheckAction, call.body)
            meta = _Call(call.session, call.correlation_id, canonical, call.headers).meta(True)
            if_match = call.headers.get("if-match")
            if body.action == "finish":
                return _json(s.finish(meta, guided_check_id, if_match), call.correlation_id)
            data = ActionInput(body.action, body.client_time.root, body.step_no, body.answer)
            return _json(s.act(meta, guided_check_id, data, if_match), call.correlation_id)

        return await run(request, "actOnGuidedCheck", work)

    @router.get("/v1/help-sessions/{help_session_id}/coaching", operation_id="listCoachingSamples")
    async def list_coaching_samples(
        help_session_id: str, request: Request, cursor: str | None = None, limit: int | None = None
    ) -> Response:
        def work(call: _Call, s: TeachServices) -> Response:
            body, release_id = s.coaching.page(call.session, help_session_id, cursor, limit)
            headers = {
                "X-Correlation-Id": call.correlation_id,
                "Config-Release": release_id,
            }
            return JSONResponse(body, headers=headers)

        return await run(request, "listCoachingSamples", work)

    return router


def assemble(
    ports: TeachPorts,
    *,
    authenticator: RequestAuthenticator,
    samples: CoachingSampleRepository,
    help: HelpSessionReader | None,
) -> TeachServices:
    """One set of use cases over one set of ports; composition and the end to end tests both build it here."""
    core = TeachCore(ports)
    return TeachServices(
        authenticator=authenticator,
        ids=ports.ids,
        releases=ports.releases,
        start=StartGuidedCheck(ports),
        beat=TapBeat(ports),
        act=ActOnGuidedCheck(ports),
        finish=FinishGuidedCheck(ports),
        get=GetGuidedCheck(ports),
        coaching=CoachingSamples(core, samples, help),
    )

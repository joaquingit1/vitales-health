"""Help API (E26 6): bodies parsed through the generated contract models, responses validated against them."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.crew_broadcast_schema import CrewBroadcast
from crew_health.contracts_gen.schemas.ground_message_preview_schema import GroundMessagePreview
from crew_health.contracts_gen.schemas.ground_message_schema import GroundMessage
from crew_health.contracts_gen.schemas.help_session_schema import HelpSession
from crew_health.contracts_gen.schemas.requests_schema import (
    GroundMessageCreate,
    HelpAction,
    HelpSessionCreate,
)
from crew_health.contracts_gen.schemas.subject_summary_schema import SubjectSummary
from crew_health.help.api import errors
from crew_health.help.application.acting import ActRequest
from crew_health.help.application.opening import OPEN_OPERATION, OpenRequest
from crew_health.help.application.service import HelpService
from crew_health.help.domain.errors import HelpError
from crew_health.help.domain.replay import OfflineAnswer
from crew_health.help.domain.session import EntryKind
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN
from crew_health.kernel.uow import SessionContext

URGENT_OPERATION = "createUrgentEvent"
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_IF_MATCH = re.compile(r'^(?:W/)?"?([1-9][0-9]{0,9})"?$')


# Adapter over E02 `require_roles(operation_id)` (crew_health.identity); structural copy until it is on main.
class RoleGate(Protocol):
    def session_for(self, request: Request, operation_id: str) -> SessionContext: ...


@dataclass(frozen=True, slots=True)
class _Auth:
    """The gate's answer, carried into the handler so a refusal still leaves as the contract envelope."""

    session: SessionContext | None
    error: ProblemError | None

    def require(self) -> SessionContext:
        if self.session is not None:
            return self.session
        if self.error is not None:
            raise self.error
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)


class _Malformed(Exception):
    def __init__(self, message: str, field: str = "/") -> None:
        super().__init__(message)
        self.field = field


def _correlation(runtime: HelpRuntime, value: str | None) -> str:
    if value is not None and re.fullmatch(ID_PATTERN, value):
        return value
    try:
        return runtime.service.deps.ids.new("hlc")
    except Exception:  # noqa: BLE001 - an unbound or failing id factory still yields an envelope
        return "hlc_unassigned"


def _key(value: str | None) -> str:
    if value is None or _KEY.fullmatch(value) is None:
        raise _Malformed("Idempotency-Key is required (8 to 128 characters)", "/Idempotency-Key")
    return value


def _if_match(value: str | None) -> int | None:
    if value is None:
        return None
    match = _IF_MATCH.fullmatch(value.strip())
    if match is None:
        raise _Malformed("If-Match must be the revision", "/If-Match")
    return int(match.group(1))


async def _body[M: BaseModel](request: Request, model: type[M]) -> tuple[M, dict[str, object]]:
    try:
        raw = json.loads(await request.body())
        return model.model_validate(raw), raw
    except (ValueError, ValidationError) as error:
        raise _Malformed("the body does not match the contract") from error


def _respond(
    model: type[BaseModel],
    wire: dict[str, object],
    status: int,
    correlation: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    model.model_validate(wire)
    return JSONResponse(
        wire, status_code=status, headers={"X-Correlation-Id": correlation, **(headers or {})}
    )


async def _guard(correlation: str, call: Callable[[], JSONResponse]) -> Response:
    """Runs the synchronous use case on the threadpool: storage and bounded port waits never hold the event loop,
    so one slow command never delays an unrelated request."""
    return await run_in_threadpool(_guarded, correlation, call)


def _guarded(correlation: str, call: Callable[[], JSONResponse]) -> Response:
    try:
        return call()
    except _Malformed as error:
        fields = [{"field": error.field, "code": "MALFORMED_REQUEST", "message": str(error)}]
        return errors.envelope(ErrorCode.MALFORMED_REQUEST, str(error), correlation, field_errors=fields)
    except HelpError as error:
        return errors.from_help(error, correlation)
    except ProblemError as error:
        return errors.from_problem(error, correlation)
    except Exception:  # noqa: BLE001 - the boundary: storage and unexpected faults become a retryable envelope
        return errors.internal(correlation)


def _open_request(body: HelpSessionCreate, entry_kind: EntryKind, client_release: str | None) -> OpenRequest:
    subject_id = body.subject.person_id.root if body.subject.person_id is not None else None
    ref = body.entry.ref_id.root if body.entry.ref_id is not None else None
    path = tuple(OfflineAnswer(p.node_id.root, p.answer_index, p.at.root) for p in body.offline_path or ())
    return OpenRequest(
        subject_kind=body.subject.kind,
        subject_person_id=subject_id,
        entry_kind=entry_kind,
        entry_ref_id=ref,
        place=body.place,
        client_opened_at=body.client_opened_at.root,
        offline_path=path,
        flow_id=body.flow_id.root if body.flow_id is not None else None,
        client_release_id=client_release,
    )


def _act_request(body: HelpAction, raw: dict[str, object]) -> ActRequest:
    region = body.body_region.root if body.body_region is not None else None
    return ActRequest(
        action=body.action,
        client_time=body.client_time.root,
        node_id=body.node_id.root if body.node_id is not None else None,
        answer_index=body.answer_index,
        flow_id=body.flow_id.root if body.flow_id is not None else None,
        step_index=body.step_index,
        body_region=region.root if region is not None else None,
        place=body.place,
        note=body.note,
        text=body.text,
        disposition=body.disposition,
        body=raw,
    )


class HelpRuntime:
    """Late binding: the router is declared at registration; the service and the role gate bind after resolve."""

    def __init__(self) -> None:
        self._service: HelpService | None = None
        self._gate: RoleGate | None = None

    def bind(self, service: HelpService, gate: RoleGate | None) -> None:
        self._service, self._gate = service, gate

    @property
    def service(self) -> HelpService:
        if self._service is None:
            raise HelpError("INTERNAL_ERROR", "help is not bound yet", retryable=True)
        return self._service

    def dependency(self, operation_id: str) -> Callable[[Request], _Auth]:
        def session(request: Request) -> _Auth:
            gate = self._gate
            if gate is None:
                return _Auth(None, None)
            try:
                return _Auth(gate.session_for(request, operation_id), None)
            except ProblemError as error:
                return _Auth(None, error)

        return session


def build_router(runtime: HelpRuntime) -> APIRouter:
    router = APIRouter(tags=["Help"])
    _sessions(router, runtime)
    _ground(router, runtime)
    return router


def _sessions(router: APIRouter, runtime: HelpRuntime) -> None:
    async def open_session(
        request: Request,
        auth: _Auth,
        key: str | None,
        release: str | None,
        corr: str | None,
        alias: bool,
    ) -> Response:
        correlation = _correlation(runtime, corr)
        try:
            ctx = auth.require()
        except ProblemError as error:
            return errors.from_problem(error, correlation)
        try:
            body, _ = await _body(request, HelpSessionCreate)
        except _Malformed as error:
            return errors.envelope(ErrorCode.MALFORMED_REQUEST, str(error), correlation)

        def run() -> JSONResponse:
            kind: EntryKind = (
                "urgent_event_alias" if alias and body.entry.kind == "button" else body.entry.kind
            )
            operation = URGENT_OPERATION if alias else OPEN_OPERATION
            wire, _ = runtime.service.opening.open(
                ctx,
                _open_request(body, kind, release),
                key=_key(key),
                operation=operation,
                correlation_id=correlation,
            )
            location = {"Location": f"/v1/help-sessions/{wire['help_session_id']}"}
            return _respond(HelpSession, wire, 201, correlation, location)

        return await _guard(correlation, run)

    @router.post("/v1/help-sessions", operation_id="openHelpSession")
    async def open_help_session(
        request: Request,
        auth: Annotated[_Auth, Depends(runtime.dependency("openHelpSession"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        config_release: Annotated[str | None, Header(alias="Config-Release")] = None,
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        return await open_session(request, auth, idempotency_key, config_release, x_correlation_id, False)

    @router.post("/v1/urgent-events", operation_id="createUrgentEvent")
    async def create_urgent_event(
        request: Request,
        auth: Annotated[_Auth, Depends(runtime.dependency("createUrgentEvent"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        config_release: Annotated[str | None, Header(alias="Config-Release")] = None,
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        return await open_session(request, auth, idempotency_key, config_release, x_correlation_id, True)

    @router.get("/v1/help-sessions", operation_id="listHelpSessions")
    async def list_help_sessions(
        auth: Annotated[_Auth, Depends(runtime.dependency("listHelpSessions"))],
        state: str = "open",
        cursor: str | None = None,
        limit: int | None = None,
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)

        def run() -> JSONResponse:
            if state not in ("open", "closed") or (limit is not None and not 1 <= limit <= 200):
                raise _Malformed("state or limit out of range")
            page = runtime.service.queries.page(auth.require(), state, cursor, limit)
            for item in page.items:
                HelpSession.model_validate(item)
            body = {"items": list(page.items), "page": {"next_cursor": page.next_cursor}}
            return JSONResponse(body, headers={"X-Correlation-Id": correlation})

        return await _guard(correlation, run)

    @router.get("/v1/help-sessions/{help_session_id}", operation_id="getHelpSession")
    async def get_help_session(
        help_session_id: str,
        auth: Annotated[_Auth, Depends(runtime.dependency("getHelpSession"))],
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)

        def run() -> JSONResponse:
            wire = runtime.service.queries.get(auth.require(), help_session_id)
            return _respond(HelpSession, wire, 200, correlation, {"ETag": f'"{wire["revision"]}"'})

        return await _guard(correlation, run)

    @router.post("/v1/help-sessions/{help_session_id}/actions", operation_id="actOnHelpSession")
    async def act_on_help_session(
        help_session_id: str,
        request: Request,
        auth: Annotated[_Auth, Depends(runtime.dependency("actOnHelpSession"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)
        try:
            ctx = auth.require()
        except ProblemError as error:
            return errors.from_problem(error, correlation)
        try:
            body, raw = await _body(request, HelpAction)
        except _Malformed as error:
            fields = [{"field": error.field, "code": "MALFORMED_REQUEST", "message": str(error)}]
            return errors.envelope(ErrorCode.MALFORMED_REQUEST, str(error), correlation, field_errors=fields)

        def run() -> JSONResponse:
            key = _key(idempotency_key)
            revision = _if_match(if_match)
            wire = runtime.service.acting.act(
                ctx,
                help_session_id,
                _act_request(body, raw),
                if_match=revision,
                key=key,
                correlation_id=correlation,
            )
            return _respond(HelpSession, wire, 200, correlation, {"ETag": f'"{wire["revision"]}"'})

        return await _guard(correlation, run)

    @router.get("/v1/help-sessions/{help_session_id}/subject", operation_id="getHelpSubject")
    async def get_help_subject(
        help_session_id: str,
        auth: Annotated[_Auth, Depends(runtime.dependency("getHelpSubject"))],
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)

        def run() -> JSONResponse:
            wire = runtime.service.queries.subject_summary(auth.require(), help_session_id, correlation)
            return _respond(SubjectSummary, wire, 200, correlation, {"Cache-Control": "no-store"})

        return await _guard(correlation, run)

    @router.get("/v1/help/broadcasts", operation_id="listBroadcasts")
    async def list_broadcasts(
        auth: Annotated[_Auth, Depends(runtime.dependency("listBroadcasts"))],
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)

        def run() -> JSONResponse:
            items = runtime.service.queries.broadcasts(auth.require())
            for item in items:
                CrewBroadcast.model_validate(item)
            body = {"items": list(items), "page": {"next_cursor": None}}
            return JSONResponse(body, headers={"X-Correlation-Id": correlation})

        return await _guard(correlation, run)


def _ground(router: APIRouter, runtime: HelpRuntime) -> None:
    @router.get(
        "/v1/help-sessions/{help_session_id}/ground-message-preview", operation_id="previewGroundMessage"
    )
    async def preview_ground_message(
        help_session_id: str,
        auth: Annotated[_Auth, Depends(runtime.dependency("previewGroundMessage"))],
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)
        return await _guard(
            correlation,
            lambda: _respond(
                GroundMessagePreview,
                runtime.service.ground.preview(auth.require(), help_session_id),
                200,
                correlation,
            ),
        )

    @router.post("/v1/help-sessions/{help_session_id}/ground-messages", operation_id="queueGroundMessage")
    async def queue_ground_message(
        help_session_id: str,
        request: Request,
        auth: Annotated[_Auth, Depends(runtime.dependency("queueGroundMessage"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)
        try:
            ctx = auth.require()
        except ProblemError as error:
            return errors.from_problem(error, correlation)
        try:
            body, _ = await _body(request, GroundMessageCreate)
        except _Malformed as error:
            return errors.envelope(ErrorCode.MALFORMED_REQUEST, str(error), correlation)

        def run() -> JSONResponse:
            previewed = body.previewed_sha256.root if body.previewed_sha256 is not None else None
            wire, _ = runtime.service.ground.queue(
                ctx,
                help_session_id,
                body.extra_note,
                previewed,
                key=_key(idempotency_key),
                correlation_id=correlation,
            )
            location = {"Location": f"/v1/ground-messages/{wire['ground_message_id']}"}
            return _respond(GroundMessage, wire, 201, correlation, location)

        return await _guard(correlation, run)

    @router.get("/v1/ground-messages/{ground_message_id}", operation_id="getGroundMessage")
    async def get_ground_message(
        ground_message_id: str,
        auth: Annotated[_Auth, Depends(runtime.dependency("getGroundMessage"))],
        x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
    ) -> Response:
        correlation = _correlation(runtime, x_correlation_id)
        return await _guard(
            correlation,
            lambda: _respond(
                GroundMessage, runtime.service.ground.get(auth.require(), ground_message_id), 200, correlation
            ),
        )

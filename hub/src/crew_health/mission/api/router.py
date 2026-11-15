"""The mission operations of docs/product/api/openapi.yaml (E23 6). Bodies are parsed with the generated models;
roles come from the session only; every failure is the ErrorEnvelope through the hub's ProblemError handler.

Late bound: until composition binds the service every operation fails closed with a retryable error.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.requests_schema import (
    EnvironmentChangeAction,
    EnvironmentChangeCreate,
    MissionEventCreate,
    OverlayCreate,
    OverlayEnd,
)
from crew_health.kernel.commands import CommandIdentity, canonical_json, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, SessionContext, UnitOfWork
from crew_health.mission.application.commands import Applied, Platform, execute, problem_of
from crew_health.mission.application.queries import (
    MissionReader,
    event_model,
    overlay_model,
    profile_model,
)
from crew_health.mission.application.use_cases import (
    Caller,
    EventTimeFacts,
    Services,
    act_on_change,
    create_overlay,
    end_overlay,
    propose_change,
    record_event,
)
from crew_health.mission.domain.errors import MissionDomainError, NotFound
from crew_health.mission.domain.profile import ProfileRecord

_ETAG = re.compile(r'^"([1-9][0-9]{0,17})"$')
DEFAULT_PAGE = 50

type EventTime = Callable[[UnitOfWork, datetime, Literal["LIVE", "REPLAY", "SIM"]], EventTimeFacts]


@dataclass
class MissionApi:
    """Collaborators bound by module.py once every optional provider can be looked up."""

    services: Services | None = None
    reader: MissionReader | None = None
    snapshots: ReadSnapshotFactory | None = None
    platform: Platform | None = None
    ids: IdFactory | None = None
    event_time: EventTime | None = None
    page_max: int = DEFAULT_PAGE

    def bound(self) -> tuple[Services, MissionReader, ReadSnapshotFactory, Platform, EventTime]:
        if (
            self.services is None
            or self.reader is None
            or self.snapshots is None
            or self.platform is None
            or self.event_time is None
        ):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_bound", retryable=True)
        return self.services, self.reader, self.snapshots, self.platform, self.event_time


def _caller(session: SessionContext) -> Caller:
    return Caller(session.actor, session.person_id, frozenset(role.value for role in session.roles))


def _session(request: Request) -> SessionContext:
    value: object = getattr(request.state, "session", None)
    if not isinstance(value, SessionContext):
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return value


def _body[M: BaseModel](request: Request, model: type[M]) -> M:
    raw: object = getattr(request.state, "canonical_body", b"")
    if not isinstance(raw, bytes) or not raw:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error


def _identity(request: Request, session: SessionContext, operation: str, target: str) -> CommandIdentity:
    raw: object = getattr(request.state, "canonical_body", b"")
    if not isinstance(raw, bytes):
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    parameters = canonical_json(
        {
            "Config-Release": request.headers.get("Config-Release"),
            "If-Match": request.headers.get("If-Match"),
            "query": list(request.query_params.multi_items()),
        }
    )
    return command_identity(
        key=require_id(request.headers.get("Idempotency-Key", "")),
        actor_ref=session.actor.id,
        operation=operation,
        target=target,
        parameters_json=parameters,
        body_fingerprint=hashlib.sha256(raw).hexdigest(),
        fingerprint_kind="sha256",
    )


def _revision(request: Request) -> int:
    header = request.headers.get("If-Match")
    if header is None:
        raise ProblemError(ErrorCode.VERSION_REQUIRED, ErrorCode.VERSION_REQUIRED.value)
    found = _ETAG.fullmatch(header)
    if found is None:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return int(found.group(1))


def _page_args(request: Request, page_max: int) -> tuple[str | None, int]:
    raw = request.query_params.get("limit")
    try:
        limit = DEFAULT_PAGE if raw is None else int(raw)
    except ValueError as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    if not 1 <= limit <= 200:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    cursor = request.query_params.get("cursor")
    return cursor, min(limit, page_max)


def encode_cursor(position: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"after": position}).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        after = data["after"]
    except (ValueError, KeyError, TypeError, binascii.Error) as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    if not isinstance(after, str):
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return after


def _reply(
    body: BaseModel | dict[str, object], snapshot: ReadSnapshot, *, status: int = 200, extra: dict[str, str] | None = None
) -> JSONResponse:
    content = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
    headers = {"Config-Release": snapshot.release.release_id, **(extra or {})}
    return JSONResponse(content, status_code=status, headers=headers)


def _record_reply(record: ProfileRecord, snapshot: ReadSnapshot, session: SessionContext, status: int) -> JSONResponse:
    is_crew = any(role.value == "crew" for role in session.roles)
    model = profile_model(record, is_crew=is_crew, person_id=session.person_id)
    extra = {"ETag": f'"{record.revision}"'}
    if status == 201:
        extra["Location"] = "/v1/environment/changes/" + record.profile_id
    return _reply(model, snapshot, status=status, extra=extra)


def _page_of[T](items: list[T], position: Callable[[T], str], limit: int, after: str | None) -> tuple[list[T], str | None]:
    start = 0
    if after is not None:
        keys = [position(item) for item in items]
        start = keys.index(after) + 1 if after in keys else len(items)
    window = items[start : start + limit]
    more = start + limit < len(items)
    return window, (encode_cursor(position(window[-1])) if more and window else None)


def _unavailable(component: str) -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, component + "_unavailable", retryable=True)


def build_router(api: MissionApi) -> APIRouter:  # noqa: C901 - one flat list of route registrations
    router = APIRouter()

    def read_environment(request: Request) -> JSONResponse:
        session = _session(request)
        services, reader, snapshots, _, _ = api.bound()
        with snapshots.snapshot() as snapshot:
            active = [r for r in reader.profiles(snapshot) if r.state == "active"]
            if not active:
                raise problem_of(NotFound("no active environment"))
            return _record_reply(active[0], snapshot, session, 200)

    router.add_api_route("/v1/environment", read_environment, methods=["GET"], operation_id="getEnvironment")

    def list_changes(request: Request) -> JSONResponse:
        session = _session(request)
        _, reader, snapshots, _, _ = api.bound()
        cursor, limit = _page_args(request, api.page_max)
        with snapshots.snapshot() as snapshot:
            records = list(reversed(reader.profiles(snapshot)))
            window, nxt = _page_of(records, lambda r: r.profile_id, limit, decode_cursor(cursor))
            is_crew = any(role.value == "crew" for role in session.roles)
            items = [
                profile_model(r, is_crew=is_crew, person_id=session.person_id).model_dump(mode="json")
                for r in window
            ]
            return _reply({"items": items, "page": {"next_cursor": nxt}}, snapshot)

    router.add_api_route(
        "/v1/environment/changes", list_changes, methods=["GET"], operation_id="listEnvironmentChanges"
    )

    def propose(request: Request) -> JSONResponse:
        session = _session(request)
        services, _, snapshots, platform, _ = api.bound()
        body = _body(request, EnvironmentChangeCreate)
        identity = _identity(request, session, "proposeEnvironmentChange", "/v1/environment/changes")
        created: list[str] = []

        def apply(uow: UnitOfWork) -> Applied:
            record = propose_change(
                services,
                uow,
                _caller(session),
                environment_id=body.environment_id.root,
                reason=body.reason,
                effective_at=None if body.effective_at is None else body.effective_at.root,
            )
            created.append(record.profile_id)
            return Applied(201, record.profile_id)

        execute(platform, session, identity, apply, release_header=request.headers.get("Config-Release"))
        with snapshots.snapshot() as snapshot:
            record = services.store.profile(snapshot, created[0])
            if record is None:
                raise problem_of(NotFound(created[0]))
            return _record_reply(record, snapshot, session, 201)

    router.add_api_route(
        "/v1/environment/changes", propose, methods=["POST"], operation_id="proposeEnvironmentChange", status_code=201
    )

    def act(request: Request, profile_id: str) -> JSONResponse:
        session = _session(request)
        services, _, snapshots, platform, _ = api.bound()
        body = _body(request, EnvironmentChangeAction)
        revision = _revision(request)
        identity = _identity(
            request, session, "actOnEnvironmentChange", "/v1/environment/changes/" + require_id(profile_id) + "/actions"
        )

        def apply(uow: UnitOfWork) -> Applied:
            record = act_on_change(
                services,
                uow,
                _caller(session),
                profile_id=profile_id,
                action=body.action,
                expected_revision=revision,
            )
            return Applied(200, record.profile_id)

        try:
            execute(platform, session, identity, apply, release_header=request.headers.get("Config-Release"))
        except ProblemError as error:
            if error.code is ErrorCode.STALE_VERSION:
                raise _stale_with_state(api, profile_id, session) from error
            raise
        with snapshots.snapshot() as snapshot:
            record = services.store.profile(snapshot, profile_id)
            if record is None:
                raise problem_of(NotFound(profile_id))
            return _record_reply(record, snapshot, session, 200)

    router.add_api_route(
        "/v1/environment/changes/{profile_id}/actions", act, methods=["POST"], operation_id="actOnEnvironmentChange"
    )
    _overlay_routes(router, api)
    _event_routes(router, api)
    _unavailable_routes(router)
    return router


def _stale_with_state(api: MissionApi, profile_id: str, session: SessionContext) -> ProblemError:
    services, _, snapshots, _, _ = api.bound()
    with snapshots.snapshot() as snapshot:
        record = services.store.profile(snapshot, profile_id)
        state: dict[str, object] | None = None
        if record is not None:
            is_crew = any(role.value == "crew" for role in session.roles)
            state = profile_model(record, is_crew=is_crew, person_id=session.person_id).model_dump(mode="json")
    return ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value, current_state=state)


def _overlay_routes(router: APIRouter, api: MissionApi) -> None:
    def list_overlays(request: Request) -> JSONResponse:
        session = _session(request)
        _, reader, snapshots, _, _ = api.bound()
        cursor, limit = _page_args(request, api.page_max)
        with snapshots.snapshot() as snapshot:
            owned = [] if session.person_id is None else list(reader.overlays_of(snapshot, (session.person_id,)))
            records = sorted(owned, key=lambda r: (r.started_at is None, r.started_at, r.overlay_id), reverse=True)
            window, nxt = _page_of(records, lambda r: r.overlay_id, limit, decode_cursor(cursor))
            items = [overlay_model(r).model_dump(mode="json") for r in window]
            return _reply({"items": items, "page": {"next_cursor": nxt}}, snapshot)

    router.add_api_route("/v1/overlays", list_overlays, methods=["GET"], operation_id="listOverlays")

    def create(request: Request) -> JSONResponse:
        session = _session(request)
        services, _, snapshots, platform, _ = api.bound()
        body = _body(request, OverlayCreate)
        identity = _identity(request, session, "createOverlay", "/v1/overlays")
        made: list[str] = []

        def apply(uow: UnitOfWork) -> Applied:
            record = create_overlay(
                services,
                uow,
                _caller(session),
                kind=body.overlay.root,
                planned_start=None if body.planned_start is None else body.planned_start.root,
                started_at=None if body.started_at is None else body.started_at.root,
                location=body.location,
            )
            made.append(record.overlay_id)
            return Applied(201, record.overlay_id)

        execute(platform, session, identity, apply, release_header=request.headers.get("Config-Release"))
        with snapshots.snapshot() as snapshot:
            record = services.store.overlay(snapshot, made[0])
            if record is None:
                raise problem_of(NotFound(made[0]))
            return _reply(
                overlay_model(record),
                snapshot,
                status=201,
                extra={"ETag": f'"{record.revision}"', "Location": "/v1/overlays/" + record.overlay_id},
            )

    router.add_api_route("/v1/overlays", create, methods=["POST"], operation_id="createOverlay", status_code=201)

    def end(request: Request, overlay_id: str) -> JSONResponse:
        session = _session(request)
        services, _, snapshots, platform, _ = api.bound()
        body = _body(request, OverlayEnd)
        revision = _revision(request)
        identity = _identity(request, session, "endOverlay", "/v1/overlays/" + require_id(overlay_id) + "/end")

        def apply(uow: UnitOfWork) -> Applied:
            record = end_overlay(
                services,
                uow,
                _caller(session),
                overlay_id=overlay_id,
                ended_at=body.ended_at.root,
                expected_revision=revision,
            )
            return Applied(200, record.overlay_id)

        execute(platform, session, identity, apply, release_header=request.headers.get("Config-Release"))
        with snapshots.snapshot() as snapshot:
            record = services.store.overlay(snapshot, overlay_id)
            if record is None:
                raise problem_of(NotFound(overlay_id))
            return _reply(overlay_model(record), snapshot, extra={"ETag": f'"{record.revision}"'})

    router.add_api_route("/v1/overlays/{overlay_id}/end", end, methods=["POST"], operation_id="endOverlay")


def _event_routes(router: APIRouter, api: MissionApi) -> None:
    def visible(record_note_owner: str, session: SessionContext, model: dict[str, object]) -> dict[str, object]:
        if record_note_owner != session.actor.id:
            model["note"] = None  # a free text note stays with the one who wrote it
        return model

    def list_events(request: Request) -> JSONResponse:
        session = _session(request)
        _, reader, snapshots, _, _ = api.bound()
        cursor, limit = _page_args(request, api.page_max)
        with snapshots.snapshot() as snapshot:
            records = list(reader.events_before(snapshot, before_event_id=decode_cursor(cursor), limit=limit + 1))
            window = records[:limit]
            nxt = encode_cursor(window[-1].event_id) if len(records) > limit and window else None
            items = [visible(r.recorded_by_id, session, event_model(r).model_dump(mode="json")) for r in window]
            return _reply({"items": items, "page": {"next_cursor": nxt}}, snapshot)

    router.add_api_route("/v1/mission-events", list_events, methods=["GET"], operation_id="listMissionEvents")

    def record(request: Request) -> JSONResponse:
        session = _session(request)
        services, reader, snapshots, platform, event_time = api.bound()
        body = _body(request, MissionEventCreate)
        identity = _identity(request, session, "recordMissionEvent", "/v1/mission-events")
        made: list[str] = []

        def apply(uow: UnitOfWork) -> Applied:
            at = body.occurred_at.root
            origin = body.origin_mode.root
            stored = record_event(
                services,
                uow,
                _caller(session),
                event_type=body.event_type.root,
                occurred_at=at,
                person_ids=tuple(p.root for p in body.person_ids),
                location=body.location,
                note=body.note,
                origin_mode=origin,
                time=event_time(uow, at, origin),
            )
            made.append(stored.event_id)
            return Applied(201, stored.event_id)

        execute(platform, session, identity, apply, release_header=request.headers.get("Config-Release"))
        with snapshots.snapshot() as snapshot:
            found = [
                r for r in reader.events_before(snapshot, before_event_id=None, limit=api.page_max) if r.event_id == made[0]
            ]
            if not found:
                raise problem_of(NotFound(made[0]))
            return _reply(
                visible(found[0].recorded_by_id, session, event_model(found[0]).model_dump(mode="json")),
                snapshot,
                status=201,
                extra={"Location": "/v1/mission-events/" + made[0]},
            )

    router.add_api_route(
        "/v1/mission-events", record, methods=["POST"], operation_id="recordMissionEvent", status_code=201
    )


def _unavailable_routes(router: APIRouter) -> None:
    """getMission, getContext and getHabitatView compose the E29 ground link, which has no provider yet.

    R-E23-6: their schemas require the link object, so none is fabricated. The failure is loud and retryable
    (E23 6 and 6.1); sensor and environment operations work without it.
    """

    def mission(request: Request) -> JSONResponse:
        raise _unavailable("ground_link")

    router.add_api_route("/v1/mission", mission, methods=["GET"], operation_id="getMission")
    router.add_api_route("/v1/context", mission, methods=["GET"], operation_id="getContext")
    router.add_api_route("/v1/views/habitat", mission, methods=["GET"], operation_id="getHabitatView")


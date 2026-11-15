"""HTTP surface of the identity module: createSession, getCurrentSession, deleteCurrentSession, listCrew.

Errors leave as the C5 envelope through E01's handler. No token, credential or private value appears in an
error, a log or a header other than the one-time Session body.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import ValidationError

from crew_health.contracts_gen.schemas.crew_member_schema import CrewMember
from crew_health.contracts_gen.schemas.requests_schema import SessionCreate
from crew_health.contracts_gen.schemas.session_schema import Session
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import RosterEntry
from crew_health.identity.application.service import IdentityService, SessionView
from crew_health.identity.domain.session import SignInRequest, roles_for, unauthenticated
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Role, SessionContext

_DEFAULT_LIMIT = 50
_CURSOR_PREFIX = "roster:"
_ROLE_ORDER = tuple(Role)


def _not_bound() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)


class IdentityRuntime:
    """Late-bound services; until the module is bound every call fails loudly and retryably."""

    def __init__(self) -> None:
        self.service_value: IdentityService | None = None
        self.admin_value: IdentityAdmin | None = None

    def service(self) -> IdentityService:
        if self.service_value is None:
            raise _not_bound()
        return self.service_value

    def admin(self) -> IdentityAdmin:
        if self.admin_value is None:
            raise _not_bound()
        return self.admin_value


def listener_of(request: Request) -> str:
    """The hub listener that received the request; a token is valid only on the listener that issued it."""
    server = request.scope.get("server")
    if isinstance(server, tuple) and len(server) == 2:
        return f"{server[0]}:{server[1]}"
    return "local"


def client_of(request: Request) -> str:
    """The transport peer address, never a forwarding header (E02 3.2)."""
    return request.client.host if request.client is not None and request.client.host else "local"


def bearer(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        return None
    return token


class IdentitySecurity:
    """E01's HttpSecurity for the composition root: bearer token to a live SessionContext on every request."""

    def __init__(self, runtime: IdentityRuntime) -> None:
        self.runtime = runtime

    def authenticate(self, request: Request) -> SessionContext:
        return self.runtime.service().authenticate(
            bearer(request.headers.get("authorization")), listener=listener_of(request)
        )

    def authorize(self, session: SessionContext, operation_id: str) -> None:
        """Role coherence after E01's operation role gate: a context whose roles do not follow from its kind
        is refused, so a station or adapter context can never carry a person or clinical role."""
        expected_kind_roles = roles_for(
            session.kind, 1 if Role.CREW_MEDICAL_OFFICER in session.roles else None
        )
        if session.roles != expected_kind_roles or (session.kind == "crew") != (
            session.person_id is not None
        ):
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


def _session(request: Request) -> SessionContext:
    value: object = getattr(request.state, "session", None)
    if not isinstance(value, SessionContext):
        raise unauthenticated()
    return value


def _correlation(request: Request) -> str:
    value: object = getattr(request.state, "correlation_id", None)
    if not isinstance(value, str):
        raise _not_bound()
    return value


def session_wire(view: SessionView) -> Session:
    record = view.record
    body: dict[str, object] = {
        "session_id": record.session_id,
        "actor": {
            "kind": _actor_kind(view),
            "id": record.principal_id,
            "display_name": view.actor_display_name,
        },
        "person": None
        if record.person_id is None or view.person_display_name is None
        else {"person_id": record.person_id, "display_name": view.person_display_name},
        "roles": [role.value for role in _ROLE_ORDER if role in view.roles],
        "mission_id": record.mission_id,
        "created_at": _instant(record.created_at),
        "expires_at": _instant(record.expires_at),
    }
    if view.token is not None:
        body["token"] = view.token
    return Session.model_validate(body)


def crew_wire(entry: RosterEntry) -> CrewMember:
    roles = roles_for("crew", entry.medical_officer_rank)
    return CrewMember.model_validate(
        {
            "person_id": entry.person_id,
            "display_name": entry.display_name,
            "roles": [role.value for role in _ROLE_ORDER if role in roles],
            "on_roster_from_day": entry.on_roster_from_day,
            "location": None,
            "active_overlays": [],
            "helping_session_id": None,
            "medical_officer_rank": entry.medical_officer_rank,
        }
    )


def encode_cursor(roster_index: int) -> str:
    return base64.urlsafe_b64encode((_CURSOR_PREFIX + str(roster_index)).encode("ascii")).decode("ascii")


def decode_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        text = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
    except binascii.Error, UnicodeError, ValueError:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from None
    digits = text.removeprefix(_CURSOR_PREFIX)
    if not text.startswith(_CURSOR_PREFIX) or not digits.isdigit() or len(digits) > 9:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return int(digits)


def _actor_kind(view: SessionView) -> Literal["person", "station", "ground", "operator", "adapter"]:
    kind = view.record.kind
    if kind == "crew":
        return "person"
    if kind == "editor":
        return "operator"
    return kind


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json(model: Session | dict[str, object], *, status: int, headers: dict[str, str]) -> Response:
    raw = (
        model.model_dump_json(exclude_unset=True)
        if isinstance(model, Session)
        else canonical_json(model).decode("utf-8")
    )
    return Response(raw, status_code=status, media_type="application/json", headers=headers)


def build_router(runtime: IdentityRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/sessions", operation_id="createSession", status_code=201)
    def create_session(request: Request) -> Response:
        service = runtime.service()
        raw: object = getattr(request.state, "parsed_json", None)
        try:
            value = SessionCreate.model_validate(raw)
        except ValidationError:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from None
        view = service.sign_in(
            SignInRequest(value.kind, None if value.person_id is None else value.person_id.root),
            value.credential,
            client=client_of(request),
            listener=listener_of(request),
            correlation_id=_correlation(request),
        )
        headers = {"Location": "/v1/sessions/current", "Config-Release": service.release_id()}
        return _json(session_wire(view), status=201, headers=headers)

    @router.get("/v1/sessions/current", operation_id="getCurrentSession")
    def current_session(request: Request) -> Response:
        service = runtime.service()
        view = service.current(_session(request))
        return _json(session_wire(view), status=200, headers={"Config-Release": service.release_id()})

    @router.delete("/v1/sessions/current", operation_id="deleteCurrentSession", status_code=204)
    def delete_session(
        request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key")]
    ) -> Response:
        if not 8 <= len(idempotency_key) <= 128:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        service = runtime.service()
        service.sign_out(_session(request), key=idempotency_key, correlation_id=_correlation(request))
        return Response(status_code=204, headers={"Config-Release": service.release_id()})

    @router.get("/v1/crew", operation_id="listCrew")
    def list_crew(request: Request, cursor: str | None = None, limit: int = _DEFAULT_LIMIT) -> Response:
        _session(request)
        service = runtime.service()
        entries, more = service.crew_page(after_index=decode_cursor(cursor), limit=limit)
        page: dict[str, object] = {
            "next_cursor": encode_cursor(entries[-1].roster_index) if more and entries else None
        }
        body: dict[str, object] = {
            "items": [crew_wire(entry).model_dump(mode="json") for entry in entries],
            "page": page,
        }
        return _json(body, status=200, headers={"Config-Release": service.release_id()})

    return router

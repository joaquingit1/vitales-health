"""The warnings operations of docs/product/api/openapi.yaml (E24 6): listWarnings, getWarning, actOnWarning.

Roles come from the session only; every failure is the ErrorEnvelope through the hub's ProblemError handler. Late
bound: until composition binds the services every operation fails closed with a retryable error.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.requests_schema import WarningAction as WarningActionBody
from crew_health.kernel.commands import CommandIdentity, canonical_json, command_identity
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import SessionContext
from crew_health.rules.application.commands import ActBody, WarningActions
from crew_health.rules.application.queries import WarningReader
from crew_health.rules.domain.vocabulary import WarningAction

_ETAG = re.compile(r'^"([1-9][0-9]{0,17})"$')


@dataclass
class RulesApi:
    reader: WarningReader | None = None
    actions: WarningActions | None = None
    page_limit: int = 100

    def bound(self) -> tuple[WarningReader, WarningActions]:
        if self.reader is None or self.actions is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "rules_not_bound", retryable=True)
        return self.reader, self.actions


def _session(request: Request) -> SessionContext:
    value: object = getattr(request.state, "session", None)
    if not isinstance(value, SessionContext):
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return value


def _malformed() -> ProblemError:
    return ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)


def _body(request: Request) -> ActBody:
    raw: object = getattr(request.state, "canonical_body", b"")
    if not isinstance(raw, bytes) or not raw:
        raise _malformed()
    try:
        loaded = json.loads(raw)
    except ValueError as error:
        raise _malformed() from error
    try:
        model = WarningActionBody.model_validate(loaded)
    except ValidationError as error:
        fields = [
            FieldError(".".join(str(p) for p in e["loc"]) or "body", "invalid", e["msg"])
            for e in error.errors()
        ]
        raise ProblemError(
            ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value, field_errors=fields
        ) from error
    return ActBody(WarningAction(model.action), model.reason, model.note)


def _identity(request: Request, session: SessionContext, warning_id: str) -> CommandIdentity:
    raw: object = getattr(request.state, "canonical_body", b"")
    if not isinstance(raw, bytes):
        raise _malformed()
    parameters = canonical_json(
        {"Config-Release": request.headers.get("Config-Release"), "If-Match": request.headers.get("If-Match")}
    )
    return command_identity(
        key=require_id(request.headers.get("Idempotency-Key", "")),
        actor_ref=session.actor.id,
        operation="actOnWarning",
        target="/v1/warnings/" + require_id(warning_id) + "/actions",
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
        raise _malformed()
    return int(found.group(1))


def _encode(position: tuple[int, str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(position)).encode()).decode().rstrip("=")


def _decode(cursor: str | None) -> tuple[int, str, str] | None:
    if cursor is None:
        return None
    try:
        rank, at, wid = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return int(rank), str(at), str(wid)
    except (ValueError, TypeError, binascii.Error) as error:
        raise _malformed() from error


def _limit(request: Request, maximum: int) -> int:
    raw = request.query_params.get("limit")
    try:
        value = maximum if raw is None else int(raw)
    except ValueError as error:
        raise _malformed() from error
    if not 1 <= value <= 200:
        raise _malformed()
    return min(value, maximum)


def build_router(api: RulesApi) -> APIRouter:
    router = APIRouter()

    def list_warnings(request: Request) -> JSONResponse:
        session = _session(request)
        reader, _ = api.bound()
        state = request.query_params.get("state", "active")
        if state not in ("active", "all"):
            raise _malformed()
        items, last = reader.listing(
            session,
            state=state,
            after=_decode(request.query_params.get("cursor")),
            limit=_limit(request, api.page_limit),
        )
        page: dict[str, object] = {"next_cursor": None if last is None else _encode(last)}
        return JSONResponse({"items": items, "page": page})

    router.add_api_route("/v1/warnings", list_warnings, methods=["GET"], operation_id="listWarnings")

    def get_warning(request: Request, warning_id: str) -> JSONResponse:
        session = _session(request)
        reader, _ = api.bound()
        body, revision = reader.get(session, require_id(warning_id))
        return JSONResponse(body, headers={"ETag": f'"{revision}"'})

    router.add_api_route("/v1/warnings/{warning_id}", get_warning, methods=["GET"], operation_id="getWarning")

    def act(request: Request, warning_id: str) -> JSONResponse:
        session = _session(request)
        _, actions = api.bound()
        body = _body(request)
        revision = _revision(request)
        identity = _identity(request, session, warning_id)
        result, new_revision = actions.act(
            session,
            warning_id,
            body,
            identity,
            if_match=revision,
            config_release=request.headers.get("Config-Release"),
        )
        # Sorted keys: the first answer and a replay of it are the same bytes (S8.6).
        return Response(
            json.dumps(result, sort_keys=True),
            media_type="application/json",
            headers={"ETag": f'"{new_revision}"'},
        )

    router.add_api_route(
        "/v1/warnings/{warning_id}/actions", act, methods=["POST"], operation_id="actOnWarning"
    )
    return router

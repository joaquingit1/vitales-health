"""The five contracted measurement operations (E05 6). Bodies are parsed with the generated models; every non-2xx
answer is the closed ErrorEnvelope (D5). Messages are codes, never private text; the client shows its own words."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.capability_schema import Capability
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.measurement_request_schema import MeasurementRequest
from crew_health.contracts_gen.schemas.requests_schema import (
    AttemptCreate,
    MeasurementRequestAction,
    MeasurementRequestCreate,
    Observation,
)
from crew_health.kernel.commands import CommandIdentity, canonical_json, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN, IdFactory
from crew_health.kernel.uow import SessionContext
from crew_health.measurement.application.commands import (
    AttemptInput,
    CommandOutcome,
    MeasurementService,
    ObservationInput,
)
from crew_health.measurement.application.errors import InvalidInput, problem_of
from crew_health.measurement.application.ports import CommandFingerprint, ProposalInput, SessionAuthenticator
from crew_health.measurement.application.queries import MeasurementQueries
from crew_health.measurement.application.representation import capability_wire
from crew_health.measurement.domain.errors import DomainRefusal

REQUESTS_PATH = "/v1/measurement-requests"
CAPABILITIES_PATH = "/v1/capabilities"
_ETAG = re.compile(r'^"([1-9][0-9]{0,17})"$')
_KEY_LENGTH = (8, 128)


@dataclass
class MeasurementApi:
    """Late bound collaborators. Until composition binds them every operation fails closed (500, retryable)."""

    service: MeasurementService | None = None
    queries: MeasurementQueries | None = None
    auth: SessionAuthenticator | None = None
    fingerprints: CommandFingerprint | None = None
    ids: IdFactory | None = None


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    body: dict[str, object]
    headers: dict[str, str]


type _Handler = Callable[[SessionContext, str], _Reply]


def build_router(api: MeasurementApi) -> APIRouter:
    router = APIRouter()

    @router.get(CAPABILITIES_PATH, operation_id="listCapabilities")
    def list_capabilities(request: Request) -> Response:
        return _run(api, request, "listCapabilities", lambda s, c: _list(api, request, s))

    @router.post(REQUESTS_PATH, operation_id="createMeasurementRequest")
    async def create_measurement_request(request: Request) -> Response:
        raw = await request.body()
        return _run(api, request, "createMeasurementRequest", lambda s, c: _create(api, request, raw, s, c))

    @router.get(REQUESTS_PATH + "/{request_id}", operation_id="getMeasurementRequest")
    def get_measurement_request(request: Request, request_id: str) -> Response:
        return _run(api, request, "getMeasurementRequest", lambda s, c: _get(api, request_id, s))

    @router.post(REQUESTS_PATH + "/{request_id}/actions", operation_id="actOnMeasurementRequest")
    async def act_on_measurement_request(request: Request, request_id: str) -> Response:
        raw = await request.body()
        return _run(
            api, request, "actOnMeasurementRequest", lambda s, c: _act(api, request, request_id, raw, s, c)
        )

    @router.post(REQUESTS_PATH + "/{request_id}/attempts", operation_id="recordAttempt")
    async def record_attempt(request: Request, request_id: str) -> Response:
        raw = await request.body()
        return _run(api, request, "recordAttempt", lambda s, c: _attempt(api, request, request_id, raw, s, c))

    return router


# Boundary plumbing ----------------------------------------------------------------------------------------------


def _run(api: MeasurementApi, request: Request, operation: str, handler: _Handler) -> Response:
    correlation = _correlation(api, request)
    try:
        if api.auth is None or api.service is None or api.queries is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "measurement_not_bound", retryable=True)
        session = api.auth.authenticate({k.lower(): v for k, v in request.headers.items()}, operation)
        if session is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        reply = handler(session, correlation)
    except ProblemError as error:
        return _envelope(error, correlation)
    except DomainRefusal as refusal:
        return _envelope(problem_of(refusal), correlation)
    except Exception:  # noqa: BLE001  (SYSTEM 5.5: an unknown failure leaves as INTERNAL_ERROR, no detail)
        return _envelope(ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value), correlation)
    headers = {"X-Correlation-Id": correlation, **reply.headers}
    return JSONResponse(status_code=reply.status, content=reply.body, headers=headers)


def _correlation(api: MeasurementApi, request: Request) -> str:
    given = request.headers.get("x-correlation-id")
    if given is not None and re.fullmatch(ID_PATTERN, given) is not None:
        return given
    if api.ids is None:
        return "cor_unbound"
    return api.ids.new("cor")


def _envelope(error: ProblemError, correlation: str) -> JSONResponse:
    body: dict[str, object] = {
        "code": error.code.value,
        "message": error.code.value,
        "retryable": error.retryable,
        "field_errors": [
            {"field": f.field, "code": f.code, "message": f.message} for f in error.field_errors
        ],
        "correlation_id": correlation,
        "current_state": error.current_state,
        "supported_alternative": error.supported_alternative,
    }
    ErrorEnvelope.model_validate(body)
    return JSONResponse(status_code=error.status, content=body, headers={"X-Correlation-Id": correlation})


def _reject_constant(name: str) -> object:
    raise ValueError("non-finite number " + name)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_body(request: Request, raw: bytes) -> dict[str, object]:
    """Exact numbers (Decimal), no NaN or infinity, no duplicate keys, an object at the top."""
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != "application/json":
        raise ProblemError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, ErrorCode.UNSUPPORTED_MEDIA_TYPE.value)
    try:
        body = json.loads(
            raw, parse_float=Decimal, parse_constant=_reject_constant, object_pairs_hook=_unique
        )
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    if not isinstance(body, dict):
        raise InvalidInput("/", "object_required")
    return cast(dict[str, object], body)


def _parse[M: BaseModel](model: type[M], body: dict[str, object]) -> M:
    try:
        return model.model_validate(body)
    except ValidationError as error:
        first = error.errors()[0]
        location = [str(p) for p in first["loc"] if not str(p).startswith("function-")]
        pointer = "/" + "/".join(location) if location else "/"
        raise InvalidInput(pointer, "schema") from error


def _no_null(body: object, fields: tuple[str, ...], prefix: str = "") -> None:
    """The generated models accept null for optional fields the schema does not let be null."""
    if isinstance(body, dict):
        for name in fields:
            if name in body and body[name] is None:
                raise InvalidInput(prefix + "/" + name, "null_not_allowed")


def _plain(value: object) -> object:
    """Exact text form of numbers, for the body fingerprint only."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _identity(
    api: MeasurementApi,
    request: Request,
    session: SessionContext,
    operation: str,
    target: str,
    body: dict[str, object],
) -> CommandIdentity:
    """Operation, target (path with its IDs), parameters and keyed body digest: a key reused anywhere else is a
    conflict, never a replay (SYSTEM 5.8)."""
    key = request.headers.get("idempotency-key")
    low, high = _KEY_LENGTH
    if key is None or not low <= len(key) <= high or re.fullmatch(ID_PATTERN, key) is None:
        raise InvalidInput("Idempotency-Key", "required")
    if api.fingerprints is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "fingerprint_unbound", retryable=True)
    parameters = {
        "if_match": request.headers.get("if-match"),
        "config_release": request.headers.get("config-release"),
    }
    return command_identity(
        key=key,
        actor_ref=session.actor.id,
        operation=operation,
        target=target,
        parameters_json=canonical_json(parameters),
        body_fingerprint=api.fingerprints.fingerprint(canonical_json(_plain(body))),
        fingerprint_kind="hmac_sha256",
    )


def _command_reply(outcome: CommandOutcome, *, location: bool) -> _Reply:
    MeasurementRequest.model_validate(outcome.body)
    if outcome.refusal is not None:
        raise ProblemError(ErrorCode(outcome.refusal), outcome.refusal, current_state=outcome.body)
    headers = {"Config-Release": outcome.release_id}
    if location and outcome.status == 201:
        headers["Location"] = REQUESTS_PATH + "/" + outcome.request_id
    return _Reply(outcome.status, outcome.body, headers)


# Operations -----------------------------------------------------------------------------------------------------


def _list(api: MeasurementApi, request: Request, session: SessionContext) -> _Reply:
    queries = cast(MeasurementQueries, api.queries)
    limit_text = request.query_params.get("limit")
    limit: int | None = None
    if limit_text is not None:
        if not limit_text.isdigit() or len(limit_text) > 4:
            raise InvalidInput("limit", "integer")
        limit = int(limit_text)
    result = queries.list_capabilities(session, cursor=request.query_params.get("cursor"), limit=limit)
    items = [capability_wire(c) for c in result.items]
    for item in items:
        Capability.model_validate(item)
    body: dict[str, object] = {
        "items": items,
        "page": {"next_cursor": result.next_cursor, "total": result.total},
    }
    return _Reply(200, body, {"Config-Release": result.release_id})


def _create(
    api: MeasurementApi, request: Request, raw: bytes, session: SessionContext, correlation: str
) -> _Reply:
    body = _json_body(request, raw)
    _no_null(body, ("operator_id", "condition_ids"))
    parsed = _parse(MeasurementRequestCreate, body)
    conditions = tuple(c.root for c in parsed.condition_ids or ())
    if len(set(conditions)) != len(conditions):
        raise InvalidInput("/condition_ids", "unique_items")
    identity = _identity(api, request, session, "createMeasurementRequest", REQUESTS_PATH, body)
    region = None if parsed.body_region is None else parsed.body_region.root
    proposal = ProposalInput(
        episode_id=None if parsed.episode_id is None else parsed.episode_id.root,
        capability_code=parsed.capability_code.root,
        reason=parsed.reason,
        decision_affected=parsed.decision_affected,
        operator_id=None if parsed.operator_id is None else parsed.operator_id.root,
        body_region=None if region is None else region.root,
        body_region_given="body_region" in body,
        condition_ids=conditions,
    )
    service = cast(MeasurementService, api.service)
    outcome = service.create(
        session,
        identity,
        proposal,
        correlation_id=correlation,
        config_release=request.headers.get("config-release"),
    )
    return _command_reply(outcome, location=True)


def _get(api: MeasurementApi, request_id: str, session: SessionContext) -> _Reply:
    queries = cast(MeasurementQueries, api.queries)
    view = queries.get_request(session, request_id)
    MeasurementRequest.model_validate(view.body)
    return _Reply(200, view.body, {"Config-Release": view.release_id, "ETag": f'"{view.revision}"'})


def _act(
    api: MeasurementApi,
    request: Request,
    request_id: str,
    raw: bytes,
    session: SessionContext,
    correlation: str,
) -> _Reply:
    body = _json_body(request, raw)
    _no_null(body, ("reason",))
    parsed = _parse(MeasurementRequestAction, body)
    target = f"{REQUESTS_PATH}/{request_id}/actions"
    identity = _identity(api, request, session, "actOnMeasurementRequest", target, body)
    revision: int | None = None
    if_match = request.headers.get("if-match")
    if if_match is not None:
        match = _ETAG.fullmatch(if_match.strip())
        if match is None:
            raise InvalidInput("If-Match", "etag")
        revision = int(match.group(1))
    service = cast(MeasurementService, api.service)
    outcome = service.act(
        session,
        identity,
        request_id,
        if_match=revision,
        action=parsed.action,
        correlation_id=correlation,
        config_release=request.headers.get("config-release"),
    )
    return _command_reply(outcome, location=False)


def _attempt(
    api: MeasurementApi,
    request: Request,
    request_id: str,
    raw: bytes,
    session: SessionContext,
    correlation: str,
) -> _Reply:
    body = _json_body(request, raw)
    _no_null(body, ("failure_reason", "quality_notes"))
    parsed = _parse(AttemptCreate, body)
    raw_observations = body.get("observations")
    if not isinstance(raw_observations, list) or len(raw_observations) != len(parsed.observations):
        raise InvalidInput("/observations", "schema")
    observations = tuple(
        _observation(item, model, f"/observations/{index}")
        for index, (item, model) in enumerate(zip(raw_observations, parsed.observations, strict=True))
    )
    target = f"{REQUESTS_PATH}/{request_id}/attempts"
    identity = _identity(api, request, session, "recordAttempt", target, body)
    attempt = AttemptInput(
        sequence=parsed.sequence,
        started_at=parsed.started_at.root,
        ended_at=parsed.ended_at.root,
        outcome=parsed.outcome,
        failure_reason=parsed.failure_reason,
        observations=observations,
        quality_notes=tuple(n.root for n in parsed.quality_notes or ()),
        fingerprint=identity.body_fingerprint,
    )
    service = cast(MeasurementService, api.service)
    outcome = service.record_attempt(session, identity, request_id, attempt, correlation_id=correlation)
    return _command_reply(outcome, location=False)


def _observation(item: object, model: Observation, at: str) -> ObservationInput:
    """Values keep their exact decimal form from the wire; a bool or string never passes as a number."""
    _no_null(item, ("context", "supersedes", "measurement_request_id"), at)
    value = item.get("value") if isinstance(item, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise InvalidInput(at + "/value", "number")
    region = None if model.body_region is None else model.body_region.root
    return ObservationInput(
        patient_id=model.patient_id.root,
        episode_id=None if model.episode_id is None else model.episode_id.root,
        code=model.code.root,
        value=Decimal(value),
        unit=model.unit,
        observed_at=model.observed_at.root,
        origin_mode=model.origin_mode.root,
        acquisition_method=model.acquisition_method.root,
        device_id=None if model.device_id is None else model.device_id.root,
        context=tuple(sorted((model.context or {}).items())),
        body_region=None if region is None else region.root,
        supersedes=None if model.supersedes is None else model.supersedes.root,
        measurement_request_id=(
            None if model.measurement_request_id is None else model.measurement_request_id.root
        ),
    )

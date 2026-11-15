"""The sensor operations of docs/product/api/openapi.yaml (E23 6). Roles come from the session (the hub's
middleware has already matched `x-roles`); every failure is the ErrorEnvelope. Reads open one snapshot."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.common_schema import Instant
from crew_health.kernel.commands import CommandIdentity, canonical_json, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWork
from crew_health.sensors.application.commands import Applied, Platform, execute
from crew_health.sensors.application.ingest import IngestOutcome, check_envelope, ingest
from crew_health.sensors.application.queries import (
    channel_model,
    iso,
    profile_sensor_types,
    reading_model,
    series_model,
    status_model,
)
from crew_health.sensors.application.runtime import Runtime
from crew_health.sensors.domain.errors import SensorDomainError

import base64
import binascii
import json

DEFAULT_PAGE = 50


@dataclass
class SensorsApi:
    runtime: Runtime | None = None
    snapshots: ReadSnapshotFactory | None = None
    platform: Platform | None = None

    def bound(self) -> tuple[Runtime, ReadSnapshotFactory, Platform]:
        if self.runtime is None or self.snapshots is None or self.platform is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "sensors_not_bound", retryable=True)
        return self.runtime, self.snapshots, self.platform


def _session(request: Request) -> SessionContext:
    value: object = getattr(request.state, "session", None)
    if not isinstance(value, SessionContext):
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return value


def _reply(body: object, view: object, status: int = 200) -> JSONResponse:
    content = body.model_dump(mode="json", by_alias=True, exclude_unset=True) if hasattr(body, "model_dump") else body  # type: ignore[union-attr]
    release = view.release.release_id  # type: ignore[attr-defined]
    return JSONResponse(content, status_code=status, headers={"Config-Release": release})


def _cursor(request: Request, page_max: int) -> tuple[str | None, int]:
    raw = request.query_params.get("limit")
    try:
        limit = DEFAULT_PAGE if raw is None else int(raw)
    except ValueError as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    if not 1 <= limit <= 200:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    cursor = request.query_params.get("cursor")
    after = None
    if cursor is not None:
        try:
            after = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))["after"]
        except (ValueError, KeyError, TypeError, binascii.Error) as error:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    return after, min(limit, page_max)


def _page(keys: list[str], items: list[dict[str, object]], after: str | None, limit: int) -> dict[str, object]:
    start = 0 if after is None else (keys.index(after) + 1 if after in keys else len(keys))
    window = items[start : start + limit]
    more = start + limit < len(items)
    nxt = None
    if more and window:
        nxt = base64.urlsafe_b64encode(json.dumps({"after": keys[start + limit - 1]}).encode()).decode().rstrip("=")
    return {"items": window, "page": {"next_cursor": nxt}}


def _instant(request: Request, name: str) -> datetime | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        return Instant.model_validate(raw).root
    except ValidationError as error:
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value) from error


def _identity(request: Request, session: SessionContext, adapter_id: str, batch_raw: bytes) -> CommandIdentity:
    parameters = canonical_json(
        {
            "Config-Release": request.headers.get("Config-Release"),
            "query": list(request.query_params.multi_items()),
        }
    )
    return command_identity(
        key=require_id(request.headers.get("Idempotency-Key", "")),
        actor_ref=session.actor.id,
        operation="ingestReadings",
        target="/v1/adapters/" + require_id(adapter_id) + "/readings",
        parameters_json=parameters,
        body_fingerprint=hashlib.sha256(batch_raw).hexdigest(),
        fingerprint_kind="sha256",
    )


def _result(outcome: IngestOutcome) -> dict[str, object]:
    return {
        "batch_id": outcome.batch_id,
        "accepted": outcome.accepted,
        "duplicates": outcome.duplicates,
        "reading_ids": list(outcome.reading_ids),
        "rejected": [{"index": i, "code": c, "message": m} for i, c, m in outcome.rejected],
        "evaluations_changed": list(outcome.evaluations_changed),
    }


def build_router(api: SensorsApi) -> APIRouter:
    router = APIRouter()

    def sensors(request: Request) -> JSONResponse:
        _session(request)
        rt, snapshots, _ = api.bound()
        after, limit = _cursor(request, rt.limits.page_max)
        with snapshots.snapshot() as view:
            env = rt.store.environment(view)
            if env is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_initialized", retryable=True)
            kinds = list(profile_sensor_types(view, env.environment_id))
            items = [status_model(rt, view, k).model_dump(mode="json", exclude_unset=True) for k in kinds]
            return _reply(_page(kinds, items, after, limit), view)

    router.add_api_route("/v1/sensors", sensors, methods=["GET"], operation_id="listSensorStatus")

    def sensor(request: Request, sensor_type_id: str) -> JSONResponse:
        _session(request)
        rt, snapshots, _ = api.bound()
        with snapshots.snapshot() as view:
            env = rt.store.environment(view)
            if rt.config_of(view.release).sensor_type(sensor_type_id) is None:
                raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
            if env is not None and sensor_type_id not in profile_sensor_types(view, env.environment_id):
                raise ProblemError(ErrorCode.CONTEXT_INAPPLICABLE, ErrorCode.CONTEXT_INAPPLICABLE.value)
            return _reply(status_model(rt, view, sensor_type_id), view)

    router.add_api_route("/v1/sensors/{sensor_type_id}", sensor, methods=["GET"], operation_id="getSensorStatus")

    def series(request: Request, sensor_type_id: str) -> JSONResponse:
        _session(request)
        rt, snapshots, _ = api.bound()
        since, until = _instant(request, "from"), _instant(request, "to")
        with snapshots.snapshot() as view:
            return _reply(series_model(rt, view, sensor_type_id, since=since, until=until), view)

    router.add_api_route("/v1/sensors/{sensor_type_id}/series", series, methods=["GET"], operation_id="getSensorSeries")

    def channels(request: Request) -> JSONResponse:
        _session(request)
        rt, snapshots, _ = api.bound()
        after, limit = _cursor(request, rt.limits.page_max)
        with snapshots.snapshot() as view:
            env = rt.store.environment(view)
            states = list(rt.store.channels(view))
            keys = [c.facts.channel_id for c in states]
            items = [channel_model(view, c, env).model_dump(mode="json", exclude_unset=True) for c in states]
            return _reply(_page(keys, items, after, limit), view)

    router.add_api_route("/v1/sensor-channels", channels, methods=["GET"], operation_id="listSensorChannels")

    def reading(request: Request, reading_id: str) -> JSONResponse:
        _session(request)
        rt, snapshots, _ = api.bound()
        with snapshots.snapshot() as view:
            record = rt.store.reading(view, reading_id)
            state = None if record is None else rt.store.channel(view, record.channel_id)
            if record is None or state is None:
                raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
            return _reply(reading_model(record, state, rt.config_of(view.release)), view)

    router.add_api_route("/v1/sensor-readings/{reading_id}", reading, methods=["GET"], operation_id="getSensorReading")

    def ingest_route(request: Request, adapter_id: str) -> JSONResponse:
        session = _session(request)
        rt, snapshots, platform = api.bound()
        if session.kind != "adapter" or session.actor.id != adapter_id:
            raise ProblemError(ErrorCode.ADAPTER_NOT_TRUSTED, ErrorCode.ADAPTER_NOT_TRUSTED.value)
        raw: object = getattr(request.state, "canonical_body", b"")
        parsed: object = getattr(request.state, "parsed_json", None)
        if not isinstance(raw, bytes):
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        batch_id, rows = check_envelope(parsed, rt.limits.batch_max_readings)
        identity = _identity(request, session, adapter_id, raw)
        done: list[IngestOutcome] = []

        def apply(uow: UnitOfWork) -> Applied:
            outcome = ingest(rt, uow, adapter_id=adapter_id, batch_id=batch_id, rows=rows)
            done.append(outcome)
            return Applied(200, None)

        execute(platform, session, identity, apply, release_header=request.headers.get("Config-Release"))
        with snapshots.snapshot() as view:
            return _reply(_result(done[0]), view)

    router.add_api_route("/v1/adapters/{adapter_id}/readings", ingest_route, methods=["POST"], operation_id="ingestReadings")
    return router


__all__ = ["SensorDomainError", "SensorsApi", "build_router", "iso"]

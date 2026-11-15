"""FastAPI surface of the camera worker (protocol in adapters/protocol.py). Routes are plain functions: FastAPI runs
them in its thread pool, and none of them captures: capture runs on the worker's runner."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health_camera.adapters.protocol import (
    CameraReply,
    CancelReply,
    CancelRequest,
    DescribeReply,
    ErrorReply,
    OutcomeReply,
    Refused,
    StartRequest,
    StatusReply,
    View,
)
from crew_health_camera.application.attempt import CAMERA_METHOD
from crew_health_camera.application.model import AcquisitionRefused, Outcome, StartSpec, StatusView
from crew_health_camera.application.service import AcquisitionService
from crew_health_camera.domain.analysis import Reading
from crew_health_camera.domain.selection import Selection

_STATUS = {
    "UNAUTHORIZED": 401,
    "INVALID": 422,
    "CONFLICT": 409,
    "BUSY": 429,
    "UNSUPPORTED": 422,
    "NO_CAMERA": 409,
    "NOT_PAIRED": 409,
    "ORIGIN_MISMATCH": 409,
}
type Reply = JSONResponse | StatusReply | CancelReply | DescribeReply | dict[str, str]


def _error(code: str, detail: str, status: int) -> JSONResponse:
    body = ErrorReply.model_validate({"code": code, "detail": detail})
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


def _view(reading: Reading, names: dict[str, str], selected: Reading) -> View:
    return View(
        device_id=reading.device_id,
        camera_name=names.get(reading.device_id, reading.device_id),
        value=str(reading.value),
        unit=reading.unit,
        selected=reading is selected,
    )


def outcome_reply(service: AcquisitionService, code: str, outcome: Outcome) -> OutcomeReply:
    policy, result, names = service.policy, outcome.result, dict(outcome.camera_names)
    refused = [
        Refused(device_id=r.device_id, reason=r.reason, reason_text=policy.reasons[r.reason])
        for r in result.refused
    ]
    common: dict[str, object] = {
        "capability_code": code,
        "origin_mode": outcome.origin_mode,
        "started_at": outcome.started_at,
        "ended_at": outcome.ended_at,
        "refused": refused,
        "quality_rule": f"{policy.policy_id}@{policy.version}",
    }
    if isinstance(result, Selection):
        views = [_view(r, names, result.selected) for r in (result.selected, *result.others)]
        common |= {
            "kind": "reading", "selected": views[0], "views": views, "reason": None, "reason_text": None,
            "selection_reason": result.reason, "disagree": result.disagree, "spread": str(result.spread),
        }  # fmt: skip
    else:
        common |= {
            "kind": "no_reading", "selected": None, "views": [], "reason": result.reason,
            "reason_text": policy.reasons[result.reason], "selection_reason": None, "disagree": False, "spread": None,
        }  # fmt: skip
    return OutcomeReply.model_validate(common)


def status_reply(service: AcquisitionService, view: StatusView) -> StatusReply:
    return StatusReply(
        acquisition_id=view.acquisition_id,
        receipt_id=view.receipt_id,
        kind=view.kind,
        state=view.state,
        outcome=None if view.outcome is None else outcome_reply(service, view.capability_code, view.outcome),
        delivery_code=view.delivery_code,
        clock_uncertainty_ms=view.clock_uncertainty_ms,
    )


def spec_of(body: StartRequest) -> StartSpec:
    return StartSpec(
        acquisition_id=body.acquisition_id,
        kind=body.kind,
        capability_code=body.capability_code,
        request_id=body.request_id,
        patient_id=body.patient_id,
        sequence=body.sequence,
        device_ids=tuple(body.device_ids),
        window_start=body.window_start,
        window_end=body.window_end,
        required_origin=body.required_origin,
        ttl_ms=body.ttl_ms,
        clock_offset_ms=body.clock_offset_ms,
        clock_uncertainty_ms=body.clock_uncertainty_ms,
        warmup_max_ms=body.warmup_max_ms,
        persist=body.persist,
    )


def build_app(
    service: AcquisitionService, *, adapter_id: str, token: str, max_body_bytes: int = 65_536
) -> FastAPI:
    """token is the hub to worker secret loaded from a protected file; an empty token refuses to build. Every route
    checks the credential first: only `Bearer <token>` is accepted (m5), and a start's body is read, bounded and
    validated only after that."""
    if not token:
        raise ValueError("worker token missing")
    app = FastAPI(title="crew-health-camera", openapi_url=None, docs_url=None, redoc_url=None)

    def authorized(header: str | None) -> bool:
        scheme, _, presented = (header or "").partition(" ")
        if scheme != "Bearer" or not presented:
            return False
        return hmac.compare_digest(presented.encode(), token.encode())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/describe", response_model=None)
    def describe(authorization: Annotated[str | None, Header()] = None) -> Reply:
        if not authorized(authorization):
            return _error("UNAUTHORIZED", "token", 401)
        cameras = service.cameras()
        policy = service.policy
        return DescribeReply(
            protocol_version="1",
            adapter_id=adapter_id,
            origin_mode=service.source.origin_mode,
            policy=f"{policy.policy_id}@{policy.version}",
            cameras=[
                CameraReply(device_id=c.device_id, name=c.name, kind=c.kind, subject_id=c.subject_id)
                for c in cameras
            ],
            ready_methods=[CAMERA_METHOD] if cameras else [],
            capability_codes=sorted({policy.pulse.signal.code, policy.breathing.code}) if cameras else [],
            max_ttl_ms=service.bounds.max_ttl_ms,
            worker_utc=service.clock.utc_now(),
        )

    @app.post("/v1/acquisitions", status_code=202, response_model=None)
    async def start(request: Request) -> Reply:
        if not authorized(request.headers.get("authorization")):
            return _error("UNAUTHORIZED", "token", 401)
        raw = await bounded(request)
        if raw is None:
            return _error("INVALID", "body_too_large", 422)
        try:
            body = StartRequest.model_validate_json(raw)
        except ValidationError:
            return _error("INVALID", "body", 422)
        return _started(body)

    async def bounded(request: Request) -> bytes | None:
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > max_body_bytes:
                return None
        return raw

    def _started(body: StartRequest) -> Reply:
        try:
            view = service.start(spec_of(body))
        except AcquisitionRefused as refused:
            return _error(refused.code, refused.detail, _STATUS[refused.code])
        return status_reply(service, view)

    @app.get("/v1/acquisitions/{acquisition_id}", response_model=None)
    def status(acquisition_id: str, authorization: Annotated[str | None, Header()] = None) -> Reply:
        if not authorized(authorization):
            return _error("UNAUTHORIZED", "token", 401)
        view = service.status(acquisition_id)
        if view is None:
            return _error("NOT_FOUND", "acquisition", 404)
        return status_reply(service, view)

    @app.post("/v1/acquisitions/{acquisition_id}/cancel", response_model=None)
    async def cancel(acquisition_id: str, request: Request) -> Reply:
        if not authorized(request.headers.get("authorization")):
            return _error("UNAUTHORIZED", "token", 401)
        raw = await bounded(request)
        if raw is None:
            return _error("INVALID", "body_too_large", 422)
        try:
            body = CancelRequest.model_validate_json(raw) if raw.strip() else CancelRequest()
        except ValidationError:
            return _error("INVALID", "body", 422)
        result = service.cancel(acquisition_id, recorded_observation_id=body.recorded_observation_id)
        return CancelReply.model_validate({"acquisition_id": acquisition_id, "result": result})

    return app

"""Hub to camera worker protocol, version 1 (coordinator decision A7: E06 owns it).

Routes (all JSON, bounded bodies, `Authorization: Bearer <hub to worker token>` except `GET /health`; a missing or
malformed credential is 401 before the body is read):

- `GET /health`: liveness only, `{"status": "ok"}`.
- `GET /v1/describe`: protocol version, adapter ID, source origin, policy, cameras with the subject each one is paired
  with, ready methods and capability codes, the longest acquisition lifetime it accepts, and the worker's UTC (the
  hub's offset estimate).
- `POST /v1/acquisitions`: start (StartRequest). 202 with StatusReply; the same start again returns the same receipt
  (the clock probe fields and warmup_max_ms, the warm up bound, are not part of its identity); another start under
  the same ID is 409 CONFLICT; a start naming no camera or a camera not paired with its subject is 409 NOT_PAIRED; a start for another origin is 409
  ORIGIN_MISMATCH; a full worker is 429 BUSY before any capture.
- `GET /v1/acquisitions/{acquisition_id}`: StatusReply, or 404 NOT_FOUND (unknown: the worker restarted or expired it).
- `POST /v1/acquisitions/{acquisition_id}/cancel`: optional CancelRequest; CancelReply with stopped, cannot_stop or
  unknown. A persisted reference reading answers cannot_stop until the cancel names the observation the hub recorded.

Errors use `{"code", "detail"}` with codes UNAUTHORIZED, INVALID, CONFLICT, BUSY, UNSUPPORTED, NO_CAMERA, NOT_PAIRED,
ORIGIN_MISMATCH, NOT_FOUND.
Values cross as decimal text with their UCUM unit; frames never cross. The JSON schema of these models is published
next to the package as protocol.schema.json and checked by tests on both sides.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1"
ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Origin = Literal["LIVE", "REPLAY", "SIM"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StartRequest(_Closed):
    acquisition_id: ID
    kind: Literal["measurement", "reference"]
    capability_code: ID
    request_id: ID | None = None
    patient_id: ID | None = None
    sequence: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    device_ids: Annotated[list[ID], Field(max_length=16)] = []
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    required_origin: Origin | None = None
    ttl_ms: Annotated[int, Field(ge=1, le=3_600_000)]
    clock_offset_ms: Annotated[int, Field(ge=-86_400_000, le=86_400_000)] = 0
    clock_uncertainty_ms: Annotated[int, Field(ge=0, le=86_400_000)] = 0
    warmup_max_ms: Annotated[int, Field(ge=1_000, le=60_000)]
    persist: bool = False


class View(_Closed):
    device_id: ID
    camera_name: str
    value: str
    unit: str
    selected: bool


class Refused(_Closed):
    device_id: ID
    reason: str
    reason_text: str


class OutcomeReply(_Closed):
    kind: Literal["reading", "no_reading"]
    capability_code: ID
    origin_mode: Origin
    started_at: datetime
    ended_at: datetime
    selected: View | None
    views: list[View]
    reason: str | None
    reason_text: str | None
    selection_reason: str | None
    disagree: bool
    spread: str | None
    refused: list[Refused]
    quality_rule: str


class StatusReply(_Closed):
    acquisition_id: ID
    receipt_id: ID
    kind: Literal["measurement", "reference"]
    state: Literal[
        "accepted",
        "capturing",
        "delivering",
        "delivered",
        "rejected",
        "reading",
        "no_reading",
        "cancelled",
        "expired",
    ]
    outcome: OutcomeReply | None
    delivery_code: str | None
    clock_uncertainty_ms: int


class CancelRequest(_Closed):
    """Optional body of a cancel. A persisted reference reading is released only when the hub names the observation
    it recorded the reading as."""

    recorded_observation_id: ID | None = None


class CancelReply(_Closed):
    acquisition_id: ID
    result: Literal["stopped", "cannot_stop", "unknown"]


class CameraReply(_Closed):
    device_id: ID
    name: str
    kind: Literal["personal", "station"]
    subject_id: ID | None


class DescribeReply(_Closed):
    protocol_version: Literal["1"]
    adapter_id: ID
    origin_mode: Origin
    policy: str
    cameras: list[CameraReply]
    ready_methods: list[str]
    capability_codes: list[str]
    max_ttl_ms: Annotated[int, Field(ge=1, le=3_600_000)]
    worker_utc: datetime


class ErrorReply(_Closed):
    code: Literal[
        "UNAUTHORIZED",
        "INVALID",
        "CONFLICT",
        "BUSY",
        "UNSUPPORTED",
        "NO_CAMERA",
        "NOT_PAIRED",
        "ORIGIN_MISMATCH",
        "NOT_FOUND",
    ]
    detail: str


MODELS: tuple[type[BaseModel], ...] = (
    StartRequest,
    StatusReply,
    CancelRequest,
    CancelReply,
    DescribeReply,
    ErrorReply,
)


def schema_document() -> dict[str, object]:
    """The published protocol.schema.json: one JSON schema per model, keyed by model name."""
    return {
        "title": "crew-health-camera protocol",
        "protocol_version": PROTOCOL_VERSION,
        "models": {model.__name__: model.model_json_schema() for model in MODELS},
    }

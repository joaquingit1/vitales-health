from __future__ import annotations

import json
import math
from collections.abc import Callable
from decimal import Decimal

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.observation_schema import Observation
from crew_health.contracts_gen.schemas.requests_schema import ObservationCreate
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity, WireQuantity
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.domain.observations import ObservationInput, ObservationRecord


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def observation_input(raw: bytes) -> ObservationInput:
    try:
        parsed = ObservationCreate.model_validate_json(raw)
        data: object = json.loads(raw, parse_float=Decimal, parse_int=Decimal, object_pairs_hook=_pairs)
        if not isinstance(data, dict) or not isinstance(data.get("value"), Decimal):
            raise ValueError("invalid number")
        original = WireQuantity(data["value"], parsed.unit)
        return ObservationInput(
            parsed.patient_id.root,
            None if parsed.episode_id is None else parsed.episode_id.root,
            parsed.code.root,
            original,
            parsed.observed_at.root,
            parsed.origin_mode.root,
            parsed.acquisition_method.root,
            None if parsed.device_id is None else parsed.device_id.root,
            tuple(sorted((parsed.context or {}).items())),
            None
            if parsed.body_region is None or parsed.body_region.root is None
            else parsed.body_region.root.root,
            None if parsed.supersedes is None else parsed.supersedes.root,
            None if parsed.measurement_request_id is None else parsed.measurement_request_id.root,
        )
    except (ValidationError, ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error


def observation_wire(value: ObservationRecord) -> bytes:
    data: dict[str, object] = {
        "schema_version": "1.0",
        "observation_id": value.observation_id,
        "patient_id": value.patient_id,
        "operator_id": value.operator_id,
        "mission_id": value.mission_id,
        "episode_id": value.episode_id,
        "code": value.code,
        "unit": value.quantity.unit.code,
        "original_unit": value.conversion.original.unit,
        "observed_at": value.observed.original_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "entered_at": value.entered_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "received_at": value.received_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "origin_mode": value.origin_mode,
        "acquisition_method": value.acquisition_method,
        "quality_status": value.quality_status,
        "quality_rule_version": value.quality_rule_version,
        "device_id": value.device_id,
        "context": dict(value.context),
        "body_region": value.body_region,
        "supersedes": value.supersedes,
    }
    result = _wire_numbers(
        data, {"value": value.quantity.value, "original_value": value.conversion.original.value}
    )
    Observation.model_validate_json(result)
    return result


def answer_wire(value: object) -> bytes:
    from crew_health.contracts_gen.schemas.answer_schema import Answer
    from crew_health.records.domain.answers import AnswerRecord

    if not isinstance(value, AnswerRecord):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    data: dict[str, object] = {
        "answer_id": value.answer_id,
        "question_id": value.question_id,
        "questionnaire_version": value.questionnaire_version,
        "presence": value.presence,
        "value": value.value.value if isinstance(value.value, Quantity) else value.value,
        "unit": value.value.unit.code
        if isinstance(value.value, Quantity) and value.value.unit.code != "1"
        else None,
        "body_region": value.body_region,
        "laterality": value.laterality,
        "actor": {"kind": value.actor.kind, "id": value.actor.id},
        "answered_at": value.answered_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "origin_mode": value.origin_mode,
        "supersedes": value.supersedes,
    }
    if isinstance(value.value, Quantity):
        del data["value"]
        result = _wire_numbers(data, {"value": value.value.value})
    else:
        result = canonical_json(data)
    Answer.model_validate_json(result)
    return result


def _wire_numbers(data: dict[str, object], numbers: dict[str, Decimal]) -> bytes:
    for number in numbers.values():
        if not number.is_finite() or not math.isfinite(float(number)):
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
    encoded = canonical_json(data)
    suffix = b"".join(
        b"," + canonical_json(key) + b":" + str(number).encode("ascii")
        for key, number in sorted(numbers.items())
    )
    return encoded[:-1] + suffix + b"}"


def create_router(
    *,
    snapshots: ReadSnapshotFactory,
    queries: RecordsQueries,
    authenticate: Callable[[Request], SessionContext],
    record: Callable[[Request, SessionContext, ObservationInput], ObservationRecord],
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/observations", operation_id="recordObservation", status_code=201)
    async def record_observation(request: Request) -> Response:
        session = authenticate(request)
        raw = await request.body()
        result = record(request, session, observation_input(raw))
        return Response(
            observation_wire(result),
            status_code=201,
            media_type="application/json",
            headers={
                "Location": "/v1/observations/" + result.observation_id,
                "Config-Release": result.release_id,
            },
        )

    @router.get("/v1/observations/{observation_id}", operation_id="getObservation")
    def get_observation(request: Request, observation_id: str) -> Response:
        session = authenticate(request)
        with snapshots.snapshot() as snapshot:
            result = queries.observation(snapshot, session, observation_id)
            return Response(
                observation_wire(result),
                media_type="application/json",
                headers={"Config-Release": result.release_id},
            )

    return router

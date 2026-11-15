from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import NormalizedTime, TimeInput, require_utc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.units import ConvertedQuantity, PressureContext, Quantity, Unit, WireQuantity
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.records.domain.answers import AnswerRecord, AnswerValue, Presence
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.observations import (
    AcquisitionMethod,
    ObservationRecord,
    OriginMode,
    QualityStatus,
)


def instant(value: datetime) -> str:
    require_utc(value)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def time_data(value: TimeInput) -> dict[str, object]:
    return {
        "original_at": instant(value.original_at),
        "source_clock_id": value.source_clock_id,
        "segment_id": value.segment_id,
        "source_clock_version": value.source_clock_version,
        "basis": value.basis,
        "origin_mode": value.origin_mode,
    }


def quantity_data(value: Quantity) -> dict[str, object]:
    return {"value": str(value.value), "unit": value.unit.code}


def observation_data(value: ObservationRecord) -> dict[str, object]:
    pressure = value.conversion.pressure
    return {
        "observation_id": value.observation_id,
        "patient_id": value.patient_id,
        "operator_id": value.operator_id,
        "mission_id": value.mission_id,
        "episode_id": value.episode_id,
        "code": value.code,
        "quantity": quantity_data(value.quantity),
        "original_value": str(value.conversion.original.value),
        "original_unit": value.conversion.original.unit,
        "conversion": value.conversion.conversion,
        "mapping_version": value.conversion.mapping_version,
        "rounding": value.conversion.rounding,
        "pressure": None
        if pressure is None
        else {
            "quantity": quantity_data(pressure.pressure),
            "basis": pressure.basis,
            "source_ref": pressure.source_ref,
            "observed_at": None if pressure.observed_at is None else instant(pressure.observed_at),
        },
        "observed": time_data(value.observed),
        "normalized": {
            "corrected_at": None
            if value.normalized.corrected_at is None
            else instant(value.normalized.corrected_at),
            "correction_id": value.normalized.correction_id,
            "uncertainty_ms": value.normalized.uncertainty_ms,
            "state": value.normalized.state,
        },
        "entered_at": instant(value.entered_at),
        "received_at": instant(value.received_at),
        "origin_mode": value.origin_mode,
        "acquisition_method": value.acquisition_method,
        "quality_status": value.quality_status,
        "quality_rule_version": value.quality_rule_version,
        "quality_reasons": list(value.quality_reasons),
        "device_id": value.device_id,
        "context": dict(value.context),
        "body_region": value.body_region,
        "supersedes": value.supersedes,
        "release_id": value.release_id,
        "proof_ref": value.proof_ref,
        "measurement_request_id": value.measurement_request_id,
        "care_basis_ref": value.care_basis_ref,
    }


def answer_data(value: AnswerRecord) -> dict[str, object]:
    return {
        "answer_id": value.answer_id,
        "question_id": value.question_id,
        "questionnaire_version": value.questionnaire_version,
        "presence": value.presence,
        "value": quantity_data(value.value) if isinstance(value.value, Quantity) else value.value,
        "body_region": value.body_region,
        "laterality": value.laterality,
        "actor": {"kind": value.actor.kind, "id": value.actor.id},
        "answered_at": instant(value.answered_at),
        "origin_mode": value.origin_mode,
        "supersedes": value.supersedes,
        "patient_id": value.patient_id,
        "mission_id": value.mission_id,
        "episode_id": value.episode_id,
        "release_id": value.release_id,
        "observed": time_data(value.observed),
        "received_at": instant(value.received_at),
        "estimate_observation_id": value.estimate_observation_id,
    }


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RecordError(ErrorCode.INTERNAL_ERROR)
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise RecordError(ErrorCode.INTERNAL_ERROR)
    return value


def _optional(value: object) -> str | None:
    return None if value is None else _text(value)


def _at(value: object) -> datetime:
    result = datetime.fromisoformat(_text(value))
    require_utc(result)
    return result


def _time(value: object) -> TimeInput:
    data = _object(value)
    version = data["source_clock_version"]
    if type(version) is not int:
        raise RecordError(ErrorCode.INTERNAL_ERROR)
    return TimeInput(
        _at(data["original_at"]),
        _text(data["source_clock_id"]),
        _text(data["segment_id"]),
        version,
        cast(Literal["source_raw", "hub_corrected"], data["basis"]),
        cast(OriginMode, data["origin_mode"]),
    )


def _quantity(value: object) -> Quantity:
    data = _object(value)
    return Quantity(Decimal(_text(data["value"])), Unit(_text(data["unit"])))


def _observation(data: dict[str, object]) -> ObservationRecord:
    normalized = _object(data["normalized"])
    normalized_time = NormalizedTime(
        _time(data["observed"]).original_at,
        None if normalized["corrected_at"] is None else _at(normalized["corrected_at"]),
        _optional(normalized["correction_id"]),
        cast(int | None, normalized["uncertainty_ms"]),
        cast(Literal["known", "unknown", "future", "replay"], normalized["state"]),
    )
    quantity = _quantity(data["quantity"])
    pressure_data = data["pressure"]
    pressure = None
    if pressure_data is not None:
        fields = _object(pressure_data)
        pressure = PressureContext(
            _quantity(fields["quantity"]),
            cast(Literal["measured", "assumed"], fields["basis"]),
            _text(fields["source_ref"]),
            None if fields["observed_at"] is None else _at(fields["observed_at"]),
        )
    conversion = ConvertedQuantity(
        WireQuantity(Decimal(_text(data["original_value"])), _text(data["original_unit"])),
        quantity,
        _text(data["conversion"]),
        _text(data["mapping_version"]),
        pressure,
        _text(data["rounding"]),
    )
    reasons = data["quality_reasons"]
    if not isinstance(reasons, list):
        raise RecordError(ErrorCode.INTERNAL_ERROR)
    return ObservationRecord(
        _text(data["observation_id"]),
        _text(data["patient_id"]),
        _text(data["operator_id"]),
        _text(data["mission_id"]),
        _optional(data["episode_id"]),
        _text(data["code"]),
        quantity,
        conversion,
        _time(data["observed"]),
        normalized_time,
        _at(data["entered_at"]),
        _at(data["received_at"]),
        cast(OriginMode, data["origin_mode"]),
        cast(AcquisitionMethod, data["acquisition_method"]),
        cast(QualityStatus, data["quality_status"]),
        _text(data["quality_rule_version"]),
        tuple(_text(r) for r in reasons),
        _optional(data["device_id"]),
        tuple(sorted((key, _text(value)) for key, value in _object(data["context"]).items())),
        _optional(data["body_region"]),
        _optional(data["supersedes"]),
        _text(data["release_id"]),
        _optional(data["proof_ref"]),
        _optional(data["measurement_request_id"]),
        _text(data["care_basis_ref"]),
    )


def _answer(data: dict[str, object]) -> AnswerRecord:
    actor = _object(data["actor"])
    raw = data["value"]
    value: AnswerValue = _quantity(raw) if isinstance(raw, dict) else cast(str | bool | None, raw)
    return AnswerRecord(
        _text(data["answer_id"]),
        _text(data["question_id"]),
        _text(data["questionnaire_version"]),
        cast(Presence, data["presence"]),
        value,
        _optional(data["body_region"]),
        _optional(data["laterality"]),
        Actor(
            cast(
                Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"],
                actor["kind"],
            ),
            _text(actor["id"]),
        ),
        _at(data["answered_at"]),
        cast(OriginMode, data["origin_mode"]),
        _optional(data["supersedes"]),
        _text(data["patient_id"]),
        _text(data["mission_id"]),
        _optional(data["episode_id"]),
        _text(data["release_id"]),
        _time(data["observed"]),
        _at(data["received_at"]),
        _optional(data["estimate_observation_id"]),
    )


def _decode(row: tuple[object, ...]) -> dict[str, object]:
    payload, digest = row
    if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != digest:
        raise RecordError(ErrorCode.INTERNAL_ERROR)
    try:
        return _object(json.loads(payload))
    except (ValueError, TypeError) as error:
        raise RecordError(ErrorCode.INTERNAL_ERROR) from error


def _replicate(
    uow: UnitOfWork, record_type: str, record_id: str, release_id: str, supersedes: str | None
) -> None:
    uow.replicate(
        ReplicationChange(
            None,
            record_type,
            record_id,
            "version" if supersedes else "create",
            canonical_json({"record_id": record_id}),
            () if supersedes is None else (supersedes,),
            release_id,
            Audience("records.owner", record_id, AudienceProjectionKey("records", record_type, 1), "1"),
        )
    )


class RecordRepository:
    def observation(self, view: ReadSnapshot | UnitOfWork, observation_id: str) -> ObservationRecord | None:
        rows = view.connection.rows(
            "SELECT payload,digest FROM records_observation WHERE observation_id=?",
            (observation_id,),
            limit=1,
        )
        return None if not rows else _observation(_decode(rows[0]))

    def answer(self, view: ReadSnapshot | UnitOfWork, answer_id: str) -> AnswerRecord | None:
        rows = view.connection.rows(
            "SELECT payload,digest FROM records_answer WHERE answer_id=?", (answer_id,), limit=1
        )
        return None if not rows else _answer(_decode(rows[0]))

    def add_observation(self, uow: UnitOfWork, value: ObservationRecord) -> None:
        payload = canonical_json(observation_data(value))
        uow.connection.execute(
            "INSERT INTO records_observation(observation_id,patient_id,mission_id,origin_mode,code,observed_at,quality_status,supersedes,payload,digest) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                value.observation_id,
                value.patient_id,
                value.mission_id,
                value.origin_mode,
                value.code,
                instant(value.normalized.corrected_at or value.observed.original_at),
                value.quality_status,
                value.supersedes,
                payload,
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        _replicate(uow, "observation", value.observation_id, value.release_id, value.supersedes)

    def add_answer(self, uow: UnitOfWork, value: AnswerRecord) -> None:
        payload = canonical_json(answer_data(value))
        uow.connection.execute(
            "INSERT INTO records_answer(answer_id,patient_id,mission_id,origin_mode,question_id,answered_at,supersedes,payload,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                value.answer_id,
                value.patient_id,
                value.mission_id,
                value.origin_mode,
                value.question_id,
                instant(value.answered_at),
                value.supersedes,
                payload,
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        _replicate(uow, "answer", value.answer_id, value.release_id, value.supersedes)

    def observations(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        mission_id: str,
        origin_mode: OriginMode,
        code: str,
        limit: int,
        accepted_only: bool = True,
        before: datetime | None = None,
    ) -> tuple[ObservationRecord, ...]:
        sql = "SELECT payload,digest FROM records_observation WHERE patient_id=? AND mission_id=? AND origin_mode=? AND code=?"
        parameters: tuple[str | int | bytes | None, ...] = (person_id, mission_id, origin_mode, code)
        if accepted_only:
            sql += " AND quality_status='accepted'"
        if before is not None:
            sql += " AND observed_at<=?"
            parameters += (instant(before),)
        sql += " ORDER BY observed_at DESC,observation_id ASC LIMIT ?"
        rows = view.connection.rows(sql, parameters + (limit,), limit=limit)
        return tuple(_observation(_decode(row)) for row in rows)

    def answers(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        mission_id: str,
        origin_mode: OriginMode,
        question_id: str,
        limit: int,
    ) -> tuple[AnswerRecord, ...]:
        rows = view.connection.rows(
            "SELECT payload,digest FROM records_answer WHERE patient_id=? AND mission_id=? AND origin_mode=? AND question_id=? ORDER BY answered_at DESC,answer_id ASC LIMIT ?",
            (person_id, mission_id, origin_mode, question_id, limit),
            limit=limit,
        )
        return tuple(_answer(_decode(row)) for row in rows)

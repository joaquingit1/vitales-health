"""SQLite repository for E05's tables, written against the kernel StorageSession (SYSTEM 5.7).

Only this file touches measurement_* tables. Every read is bounded by `rows(..., limit=...)`; an over-limit
result raises instead of truncating. Writes check affected rows: a lost compare-and-set is a loud failure.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.kernel.units import Quantity, Unit
from crew_health.measurement.application.errors import StoredInvariant
from crew_health.measurement.application.store import AcquisitionBinding, Transition
from crew_health.measurement.application.wire import format_instant, parse_instant
from crew_health.measurement.domain.model import (
    GAP_REASONS,
    REQUEST_STATES,
    Attempt,
    AttemptOutcome,
    CapabilityGap,
    Disposition,
    GapReason,
    HeldValue,
    KitCheck,
    MeasurementRequest,
    OperatorRequirement,
    OriginMode,
    Requester,
    RequesterKind,
    RequestState,
)

_REQUEST_COLUMNS = (
    "request_id, revision, episode_id, capability_code, reason, decision_affected, kit_check_json, "
    "requester_kind, requester_id, requester_display_name, patient_id, operator_id, consent_ref, state, "
    "result_observation_ids_json, body_region, created_at, origin_mode, mission_id, release_id, "
    "operator_requirement, methods_json, generation, unavailable_cause, created_by"
)
_ATTEMPT_COLUMNS = (
    "attempt_id, sequence, adapter_id, generation, started_at, ended_at, outcome, failure_reason, "
    "observation_ids_json, quality_notes_json, disposition, held_values_json, fingerprint"
)
_GAP_COLUMNS = (
    "gap_id, episode_id, capability_code, reason, consequence, alternatives_json, body_region, state, plan_id, "
    "check_another_way_step_ids_json, source_request_id, release_id, created_at"
)


class SqliteMeasurementStore:
    def __init__(self, *, max_attempts: int, max_late_attempts: int) -> None:
        if not 1 <= max_attempts <= 64 or not 1 <= max_late_attempts <= 64:
            raise StoredInvariant("measurement.attempt_bounds_outside_1_64")
        self._max_attempts = max_attempts + max_late_attempts

    # Requests -------------------------------------------------------------------------------------------

    def get_request(self, conn: StorageSession, request_id: str) -> MeasurementRequest | None:
        rows = conn.rows(
            f"SELECT {_REQUEST_COLUMNS} FROM measurement_request WHERE request_id = ?", (request_id,), limit=1
        )
        if not rows:
            return None
        attempts = conn.rows(
            f"SELECT {_ATTEMPT_COLUMNS} FROM measurement_attempt WHERE request_id = ? ORDER BY sequence, attempt_id",
            (request_id,),
            limit=self._max_attempts,
        )
        return _request(rows[0], tuple(_attempt(a) for a in attempts))

    def get_request_at(
        self, conn: StorageSession, request_id: str, revision: int
    ) -> MeasurementRequest | None:
        """The request as it stood at one revision: immutable row fields, the mutable fields recorded with that
        revision's transition, and the attempts appended up to it (the response a replay must repeat)."""
        current = self.get_request(conn, request_id)
        rows = conn.rows(
            "SELECT to_state, operator_id, consent_ref, result_observation_ids_json, generation, unavailable_cause "
            "FROM measurement_transition WHERE request_id = ? AND revision = ?",
            (request_id, revision),
            limit=1,
        )
        if current is None or not rows:
            return None
        state, operator_id, consent_ref, results, generation, cause = rows[0]
        kept = conn.rows(
            "SELECT attempt_id FROM measurement_attempt WHERE request_id = ? AND request_revision <= ?",
            (request_id, revision),
            limit=self._max_attempts,
        )
        ids = {_text(row[0]) for row in kept}
        return replace(
            current,
            revision=revision,
            state=_state(state),
            operator_id=_opt_text(operator_id),
            consent_ref=_opt_text(consent_ref),
            result_observation_ids=_strings(results),
            generation=_int(generation),
            unavailable_cause=None if cause is None else _gap_reason(cause),
            attempts=tuple(a for a in current.attempts if a.attempt_id in ids),
        )

    def insert_request(self, conn: StorageSession, request: MeasurementRequest) -> None:
        values = _request_values(request)
        marks = ", ".join("?" for _ in values)
        _one(
            conn.execute(
                f"INSERT INTO measurement_request ({_REQUEST_COLUMNS}) VALUES ({marks})", values
            ).affected_rows
        )

    def update_request(
        self, conn: StorageSession, request: MeasurementRequest, *, expected_revision: int
    ) -> None:
        result = conn.execute(
            "UPDATE measurement_request SET revision = ?, operator_id = ?, consent_ref = ?, state = ?, "
            "result_observation_ids_json = ?, generation = ?, unavailable_cause = ? "
            "WHERE request_id = ? AND revision = ?",
            (
                request.revision,
                request.operator_id,
                request.consent_ref,
                request.state,
                _json(list(request.result_observation_ids)),
                request.generation,
                request.unavailable_cause,
                request.request_id,
                expected_revision,
            ),
        )
        if result.affected_rows != 1:
            raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)

    # Attempts and transitions --------------------------------------------------------------------------

    def insert_attempt(
        self,
        conn: StorageSession,
        request_id: str,
        attempt: Attempt,
        *,
        recorded_at: datetime,
        request_revision: int,
    ) -> None:
        held = [
            {
                "patient_id": h.patient_id,
                "code": h.code,
                "value": str(h.quantity.value),
                "unit": h.quantity.unit.code,
                "observed_at": format_instant(h.observed_at),
                "origin_mode": h.origin_mode,
                "acquisition_method": h.acquisition_method,
                "device_id": h.device_id,
            }
            for h in attempt.held_values
        ]
        values: tuple[SqlValue, ...] = (
            attempt.attempt_id,
            request_id,
            attempt.sequence,
            attempt.adapter_id,
            attempt.generation,
            format_instant(attempt.started_at),
            None if attempt.ended_at is None else format_instant(attempt.ended_at),
            attempt.outcome,
            attempt.failure_reason,
            _json(list(attempt.observation_ids)),
            _json(list(attempt.quality_notes)),
            attempt.disposition,
            _json(held),
            attempt.fingerprint,
            format_instant(recorded_at),
            request_revision,
        )
        _one(
            conn.execute(
                "INSERT INTO measurement_attempt (attempt_id, request_id, sequence, adapter_id, generation, "
                "started_at, ended_at, outcome, failure_reason, observation_ids_json, quality_notes_json, "
                "disposition, held_values_json, fingerprint, recorded_at, request_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            ).affected_rows
        )

    def insert_transition(
        self, conn: StorageSession, transition: Transition, request: MeasurementRequest
    ) -> None:
        if (request.request_id, request.revision, request.state) != (
            transition.request_id,
            transition.revision,
            transition.to_state,
        ):
            raise StoredInvariant("transition_snapshot_mismatch")
        _one(
            conn.execute(
                "INSERT INTO measurement_transition (request_id, revision, actor_kind, actor_id, from_state, "
                "to_state, cause, attempt_id, at, operator_id, consent_ref, result_observation_ids_json, "
                "generation, unavailable_cause) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition.request_id,
                    transition.revision,
                    transition.actor.kind,
                    transition.actor.id,
                    transition.from_state,
                    transition.to_state,
                    transition.cause,
                    transition.attempt_id,
                    format_instant(transition.at),
                    request.operator_id,
                    request.consent_ref,
                    _json(list(request.result_observation_ids)),
                    request.generation,
                    request.unavailable_cause,
                ),
            ).affected_rows
        )

    # Gaps -----------------------------------------------------------------------------------------------

    def insert_gap(self, conn: StorageSession, gap: CapabilityGap) -> None:
        values: tuple[SqlValue, ...] = (
            gap.gap_id,
            gap.episode_id,
            gap.capability_code,
            gap.reason,
            gap.consequence,
            _json(list(gap.alternatives)),
            gap.body_region,
            gap.state,
            gap.plan_id,
            _json(list(gap.check_another_way_step_ids)),
            gap.source_request_id,
            gap.release_id,
            format_instant(gap.created_at),
        )
        marks = ", ".join("?" for _ in values)
        _one(
            conn.execute(
                f"INSERT INTO measurement_gap ({_GAP_COLUMNS}) VALUES ({marks})", values
            ).affected_rows
        )

    def get_gap(self, conn: StorageSession, gap_id: str) -> CapabilityGap | None:
        rows = conn.rows(f"SELECT {_GAP_COLUMNS} FROM measurement_gap WHERE gap_id = ?", (gap_id,), limit=1)
        return _gap(rows[0]) if rows else None

    def gap_for_request(self, conn: StorageSession, request_id: str) -> CapabilityGap | None:
        rows = conn.rows(
            f"SELECT {_GAP_COLUMNS} FROM measurement_gap WHERE source_request_id = ?", (request_id,), limit=1
        )
        return _gap(rows[0]) if rows else None

    def open_gap(self, conn: StorageSession, episode_id: str, capability_code: str) -> CapabilityGap | None:
        rows = conn.rows(
            f"SELECT {_GAP_COLUMNS} FROM measurement_gap WHERE episode_id = ? AND capability_code = ? "
            "AND state = 'open' AND source_request_id IS NULL ORDER BY gap_id LIMIT 1",
            (episode_id, capability_code),
            limit=1,
        )
        return _gap(rows[0]) if rows else None

    # Acquisitions ---------------------------------------------------------------------------------------

    def start_acquisition(self, conn: StorageSession, binding: AcquisitionBinding, *, at: datetime) -> None:
        _one(
            conn.execute(
                "INSERT INTO measurement_acquisition (request_id, generation, started_at, operator_id, adapter_id, "
                "method, device_id, fenced_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    binding.request_id,
                    binding.generation,
                    format_instant(at),
                    binding.operator_id,
                    binding.adapter_id,
                    binding.method,
                    binding.device_id,
                ),
            ).affected_rows
        )

    def acquisition(
        self, conn: StorageSession, request_id: str, generation: int
    ) -> AcquisitionBinding | None:
        rows = conn.rows(
            "SELECT operator_id, adapter_id, method, device_id, fenced_at FROM measurement_acquisition "
            "WHERE request_id = ? AND generation = ?",
            (request_id, generation),
            limit=1,
        )
        if not rows:
            return None
        operator_id, adapter_id, method, device_id, fenced_at = rows[0]
        return AcquisitionBinding(
            request_id,
            generation,
            _text(operator_id),
            _text(adapter_id),
            _text(method),
            _opt_text(device_id),
            fenced_at is not None,
        )

    def fence_acquisition(
        self, conn: StorageSession, request_id: str, generation: int, *, at: datetime
    ) -> None:
        conn.execute(
            "UPDATE measurement_acquisition SET fenced_at = ? WHERE request_id = ? AND generation = ? "
            "AND fenced_at IS NULL",
            (format_instant(at), request_id, generation),
        )


def _one(affected: int) -> None:
    if affected != 1:
        raise StoredInvariant("expected_exactly_one_row")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise StoredInvariant("stored_text_expected")
    return value


def _opt_text(value: SqlValue) -> str | None:
    return None if value is None else _text(value)


def _int(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise StoredInvariant("stored_integer_expected")
    return value


def _strings(value: SqlValue) -> tuple[str, ...]:
    loaded = json.loads(_text(value))
    if not isinstance(loaded, list) or not all(isinstance(v, str) for v in loaded):
        raise StoredInvariant("stored_string_list_expected")
    return tuple(cast(list[str], loaded))


def _request_values(r: MeasurementRequest) -> tuple[SqlValue, ...]:
    kit = r.kit_check
    kit_json = _json(
        {
            "can": kit.can,
            "instrument_ids": list(kit.instrument_ids),
            "missing_instrument_ids": list(kit.missing_instrument_ids),
            "condition_ids": list(kit.condition_ids),
            "cliff_urls": list(kit.cliff_urls),
            "text": kit.text,
            "kit_map_version": kit.kit_map_version,
            "environment_id": kit.environment_id,
        }
    )
    return (
        r.request_id,
        r.revision,
        r.episode_id,
        r.capability_code,
        r.reason,
        r.decision_affected,
        kit_json,
        r.requester.kind,
        r.requester.id,
        r.requester.display_name,
        r.patient_id,
        r.operator_id,
        r.consent_ref,
        r.state,
        _json(list(r.result_observation_ids)),
        r.body_region,
        format_instant(r.created_at),
        r.origin_mode,
        r.mission_id,
        r.release_id,
        r.operator_requirement,
        _json(list(r.methods)),
        r.generation,
        r.unavailable_cause,
        r.created_by,
    )


def _kit(text: SqlValue) -> KitCheck:
    raw = json.loads(_text(text))
    if not isinstance(raw, dict):
        raise StoredInvariant("stored_kit_check_object_expected")
    return KitCheck(
        can=bool(raw["can"]),
        instrument_ids=tuple(raw["instrument_ids"]),
        missing_instrument_ids=tuple(raw["missing_instrument_ids"]),
        condition_ids=tuple(raw["condition_ids"]),
        cliff_urls=tuple(raw["cliff_urls"]),
        text=str(raw["text"]),
        kit_map_version=str(raw["kit_map_version"]),
        environment_id=str(raw["environment_id"]),
    )


def _state(value: SqlValue) -> RequestState:
    text = _text(value)
    for state in REQUEST_STATES:
        if state == text:
            return state
    raise StoredInvariant("stored_state_invalid")


def _gap_reason(value: SqlValue) -> GapReason:
    text = _text(value)
    for reason in GAP_REASONS:
        if reason == text:
            return reason
    raise StoredInvariant("stored_gap_reason_invalid")


def _request(row: tuple[SqlValue, ...], attempts: tuple[Attempt, ...]) -> MeasurementRequest:
    return MeasurementRequest(
        request_id=_text(row[0]),
        revision=_int(row[1]),
        episode_id=_opt_text(row[2]),
        capability_code=_text(row[3]),
        reason=_text(row[4]),
        decision_affected=_text(row[5]),
        kit_check=_kit(row[6]),
        requester=Requester(cast(RequesterKind, _text(row[7])), _text(row[8]), _opt_text(row[9])),
        patient_id=_text(row[10]),
        operator_id=_opt_text(row[11]),
        consent_ref=_opt_text(row[12]),
        state=cast(RequestState, _text(row[13])),
        attempts=attempts,
        result_observation_ids=_strings(row[14]),
        body_region=_opt_text(row[15]),
        created_at=parse_instant(_text(row[16])),
        origin_mode=cast(OriginMode, _text(row[17])),
        mission_id=_text(row[18]),
        release_id=_text(row[19]),
        operator_requirement=cast(OperatorRequirement, _text(row[20])),
        methods=_strings(row[21]),
        generation=_int(row[22]),
        unavailable_cause=cast(GapReason | None, _opt_text(row[23])),
        created_by=_opt_text(row[24]),
    )


def _held(raw: object) -> HeldValue:
    if not isinstance(raw, dict):
        raise StoredInvariant("stored_held_value_object_expected")
    return HeldValue(
        patient_id=str(raw["patient_id"]),
        code=str(raw["code"]),
        quantity=Quantity(Decimal(str(raw["value"])), Unit(str(raw["unit"]))),
        observed_at=parse_instant(str(raw["observed_at"])),
        origin_mode=cast(OriginMode, str(raw["origin_mode"])),
        acquisition_method=str(raw["acquisition_method"]),
        device_id=None if raw["device_id"] is None else str(raw["device_id"]),
    )


def _attempt(row: tuple[SqlValue, ...]) -> Attempt:
    held_raw = json.loads(_text(row[11]))
    if not isinstance(held_raw, list):
        raise StoredInvariant("stored_held_values_list_expected")
    return Attempt(
        attempt_id=_text(row[0]),
        sequence=_int(row[1]),
        adapter_id=_text(row[2]),
        generation=_int(row[3]),
        started_at=parse_instant(_text(row[4])),
        ended_at=None if row[5] is None else parse_instant(_text(row[5])),
        outcome=cast(AttemptOutcome, _text(row[6])),
        failure_reason=_opt_text(row[7]),
        observation_ids=_strings(row[8]),
        quality_notes=_strings(row[9]),
        disposition=cast(Disposition, _text(row[10])),
        held_values=tuple(_held(h) for h in held_raw),
        fingerprint=_text(row[12]),
    )


def _gap(row: tuple[SqlValue, ...]) -> CapabilityGap:
    state: Literal["open", "addressed"] = "open" if _text(row[7]) == "open" else "addressed"
    if _text(row[7]) not in ("open", "addressed"):
        raise StoredInvariant("stored_gap_state_invalid")
    return CapabilityGap(
        gap_id=_text(row[0]),
        episode_id=_text(row[1]),
        capability_code=_text(row[2]),
        reason=cast(GapReason, _text(row[3])),
        consequence=_text(row[4]),
        alternatives=_strings(row[5]),
        body_region=_opt_text(row[6]),
        state=state,
        plan_id=_opt_text(row[8]),
        check_another_way_step_ids=_strings(row[9]),
        source_request_id=_opt_text(row[10]),
        release_id=_text(row[11]),
        created_at=parse_instant(_text(row[12])),
    )

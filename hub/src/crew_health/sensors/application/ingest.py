"""UC-S1 ingest (E23 3.8, PLAN deviations 1 and 2).

The request body is bounded and its envelope checked first; then every row is validated with its own generated
item model, so a malformed row is rejected by index while the valid rows around it survive. For each accepted row,
in request order, S5.10.2 evaluation runs once with its own immutable input before its successor is processed.
Freshness and clearing use the normalized observation time (D3); REPLAY freshness uses the receive reference of
the reading that strictly advanced E (D14). Nothing here rounds a value that evaluation reads.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.common_schema import Id
from crew_health.contracts_gen.schemas.requests_schema import Reading
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import NormalizedTime, TimeInput
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationInput as KernelInput
from crew_health.kernel.units import PressureContext, Quantity, Unit
from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.application.deadlines import note_status_change, refresh, schedule_deadline
from crew_health.sensors.application.runtime import (
    EVALUATION_UNAVAILABLE,
    EVALUATOR,
    EVALUATOR_VERSION,
    INSTANT_CONFLICT,
    SEQUENCE_CONFLICT,
    Runtime,
)
from crew_health.sensors.application.store import (
    BatchRow,
    ChannelState,
    EnvironmentRow,
    EvaluationInputRow,
    ReadingRecord,
)
from crew_health.sensors.domain.conversion import convert_wire_reading
from crew_health.sensors.domain.errors import InvalidUnit, InvalidValue
from crew_health.sensors.domain.freshness import TimeFacts
from crew_health.sensors.domain.plausibility import assess
from crew_health.sensors.domain.reading import ReadingFact, advance_window_end

_ENVELOPE = {"batch_id", "readings"}


@dataclass(frozen=True, slots=True)
class Rejected:
    index: int
    code: str
    raw: str


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    batch_id: str
    accepted: int
    duplicates: int
    reading_ids: tuple[str, ...]
    rejected: tuple[tuple[int, str, str], ...]  # index, code, message
    evaluations_changed: tuple[str, ...]


def check_envelope(body: object, limit: int) -> tuple[str, list[Any]]:
    """Bound and shape the whole request before any row: oversized is 413, a broken envelope is 400."""
    if not isinstance(body, dict) or set(body) != _ENVELOPE:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    rows = body["readings"]
    if not isinstance(rows, list):
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    if len(rows) > limit:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    try:
        batch_id = Id.model_validate(body["batch_id"]).root
    except ValidationError as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    if not rows:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return batch_id, rows


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _raw_of(row: Any, limit: int) -> str:
    text = json.dumps(row, sort_keys=True, default=str)
    if len(text.encode()) <= limit:
        return text
    return json.dumps({"truncated": True, "sha256": hashlib.sha256(text.encode()).hexdigest()})


def _parse_row(row: Any) -> tuple[Reading, Decimal, Decimal | None] | None:
    """The generated item model decides; the exact Decimal of the value is kept beside it."""
    if not isinstance(row, dict):
        return None
    try:
        model = Reading.model_validate(_jsonable(row))
        value = row["value"] if isinstance(row["value"], Decimal) else Decimal(str(row["value"]))
        pressure = row.get("cabin_pressure_mmhg")
        exact = None if pressure is None else Decimal(str(pressure["value"]))
    except ValidationError, KeyError, InvalidOperation, TypeError:
        return None
    if not value.is_finite():
        return None
    return model, value, exact


def _row_hash(row: dict[str, Any]) -> str:
    shaped = dict(row)
    shaped.setdefault("aggregation", {"kind": "sample", "window_seconds": None})
    return hashlib.sha256(json.dumps(shaped, sort_keys=True, default=str).encode()).hexdigest()


def _observed(
    rt: Runtime, uow: UnitOfWork, adapter: str, sequence: int, at: datetime, origin: str
) -> tuple[NormalizedTime, TimeInput | None, str | None]:
    unknown = NormalizedTime(at, None, None, None, "unknown")
    if rt.corrections is None or rt.segments is None:
        return unknown, None, None
    segment = rt.segments.for_sequence(uow, source_clock_id=adapter, sequence=sequence)
    if segment is None:
        return unknown, None, None
    stamp = TimeInput(at, adapter, segment.segment_id, segment.capture_offset_version, "source_raw", origin)  # type: ignore[arg-type]
    return rt.corrections.normalize(uow, stamp), stamp, segment.segment_id


def _receive(rt: Runtime, uow: UnitOfWork) -> TimeFacts:
    """D14 receive time through the hub's own capture stream (S5.2); no segment means unknown, never arrival."""
    if rt.corrections is None or rt.segments is None:
        return TimeFacts("unknown", None, None)
    segment = rt.segments.for_sequence(uow, source_clock_id=rt.hub_source_clock_id, sequence=2**53 - 1)
    if segment is None:
        return TimeFacts("unknown", None, None)
    stamp = TimeInput(
        uow.now,
        rt.hub_source_clock_id,
        segment.segment_id,
        segment.capture_offset_version,
        "hub_corrected",
        "LIVE",
    )
    n = rt.corrections.normalize(uow, stamp)
    return TimeFacts(n.state, n.corrected_at, n.uncertainty_ms)


class _Batch:
    def __init__(self, rt: Runtime, uow: UnitOfWork, adapter_id: str, batch_id: str) -> None:
        self.rt, self.uow, self.adapter_id, self.batch_id = rt, uow, adapter_id, batch_id
        self.config = rt.config_of(uow.release)
        self.messages = rt.messages_of(uow.release)
        self.environment = rt.store.environment(uow)
        self.accepted = 0
        self.duplicates = 0
        self.ids: list[str] = []
        self.rejected: list[Rejected] = []
        self.touched: dict[str, tuple[str, str]] = {}  # channel id -> (sensor type, status before)

    def reject(self, index: int, code: str, row: Any) -> None:
        self.rejected.append(Rejected(index, code, _raw_of(row, self.rt.limits.raw_row_max_bytes)))

    def row(self, index: int, row: Any) -> None:
        parsed = _parse_row(row)
        if parsed is None:
            self.reject(index, "MALFORMED_REQUEST", row)
            return
        model, value, pressure_value = parsed
        channel = self.rt.store.channel(self.uow, model.channel_id.root)
        if channel is None or not channel.trusted or channel.facts.adapter_id != self.adapter_id:
            self.reject(index, "ADAPTER_NOT_TRUSTED", row)
        elif model.origin_mode.root != channel.facts.origin_mode:
            self.reject(index, "ORIGIN_MISMATCH", row)
        elif (model.replay is None) != (channel.facts.origin_mode != "REPLAY"):
            self.reject(index, "INVALID_VALUE", row)
        else:
            self._admit(index, row, model, value, pressure_value, channel)

    def _admit(
        self,
        index: int,
        row: dict[str, Any],
        model: Reading,
        value: Decimal,
        pressure_value: Decimal | None,
        channel: ChannelState,
    ) -> None:
        rt, uow = self.rt, self.uow
        sequence = model.adapter_sequence
        body_hash = _row_hash(row)
        prior = rt.store.reading_at(uow, channel.facts.channel_id, sequence)
        if prior is not None:
            if prior.body_sha256 == body_hash:
                self.duplicates += 1
                self.ids.append(prior.reading_id)
            else:
                rt.fault(SEQUENCE_CONFLICT, uow.now)
                self.reject(index, "INVALID_VALUE", row)
            return
        kind = self.config.sensor_type(channel.facts.sensor_type_id)
        if kind is None:
            self.reject(index, "INVALID_VALUE", row)
            return
        pressure = None
        if model.cabin_pressure_mmhg is not None and pressure_value is not None:
            pressure = PressureContext(
                Quantity(pressure_value, Unit("mm[Hg]")),
                model.cabin_pressure_mmhg.basis,
                "adapter:" + self.adapter_id,
                None,
            )
        canonical = self.config.canonical_unit(kind.id)
        if canonical is None:
            self.reject(index, "INVALID_UNIT", row)
            return
        try:
            converted = convert_wire_reading(
                rt.need_converter(),
                uow.release,
                kind,
                value=value,
                unit=model.unit,
                canonical_unit=Unit(canonical),
                pressure=pressure,
            )
        except InvalidUnit:
            self.reject(index, "INVALID_UNIT", row)
            return
        except InvalidValue:
            self.reject(index, "INVALID_VALUE", row)
            return
        self._store(index, row, model, value, channel, kind, converted, body_hash)

    def _store(self, index, row, model, value, channel, kind, converted, body_hash) -> None:  # type: ignore[no-untyped-def]
        rt, uow = self.rt, self.uow
        observed_at = model.observed_at.root
        normalized, stamp, segment_id = _observed(
            rt, uow, self.adapter_id, model.adapter_sequence, observed_at, channel.facts.origin_mode
        )
        fact = ReadingFact(
            "",
            channel.facts.channel_id,
            model.adapter_sequence,
            normalized.corrected_at,
            uow.now,
            normalized.state,
            converted.converted.canonical.value,
            model.quality_status.root,
            channel.facts.origin_mode,
            segment_id,
        )
        verdict = assess(kind, fact)
        conflict = self._conflict(channel, normalized, converted.converted.canonical.value)
        aggregation = model.aggregation
        record = ReadingRecord(
            rt.ids.derived("rdg", channel.facts.channel_id, str(model.adapter_sequence)),
            channel.facts.channel_id,
            self.adapter_id,
            model.adapter_sequence,
            self.batch_id,
            converted.wire_value,
            kind.unit,
            converted.converted.canonical.value,
            value,
            model.unit,
            None if converted.conversion is None else _conversion_json(converted.conversion),
            converted.converted.mapping_version,
            "sample" if aggregation is None else aggregation.kind,
            None
            if aggregation is None or aggregation.window_seconds is None
            else aggregation.window_seconds.root,
            observed_at,
            None if model.window_start is None else model.window_start.root,
            uow.now,
            channel.facts.origin_mode,
            model.quality_status.root,
            normalized.state,
            normalized.corrected_at,
            normalized.uncertainty_ms,
            segment_id,
            None if stamp is None else stamp.source_clock_version,
            None
            if model.replay is None
            else json.dumps(
                {
                    "dataset": model.replay.dataset,
                    "original_observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "time_basis": model.replay.time_basis,
                }
            ),
            verdict.reason if verdict.status != "passed" else None,
            conflict,
            body_hash,
            uow.release.release_id,
        )
        rev = rt.store.insert_reading(uow, record)
        self.accepted += 1
        self.ids.append(record.reading_id)
        if record.quality_status == "rejected":
            return  # stored, never evaluated, and it cannot advance E or reset freshness (D14)
        self._advance(replace(record, rev=rev), channel, normalized, stamp)

    def _conflict(self, channel: ChannelState, normalized: NormalizedTime, canonical: Decimal) -> bool:
        if normalized.corrected_at is None:
            return False
        peers = self.rt.store.instant_peers(
            self.uow,
            channel.facts.channel_id,
            normalized.corrected_at,
            channel.facts.origin_mode,
            channel.facts.dataset,
        )
        if any(p.canonical != canonical for p in peers):
            self.rt.fault(INSTANT_CONFLICT, self.uow.now)
            return True
        return False

    def _advance(
        self,
        record: ReadingRecord,
        channel: ChannelState,
        normalized: NormalizedTime,
        stamp: TimeInput | None,
    ) -> None:
        rt, uow = self.rt, self.uow
        before = _signature(rt, uow, channel)
        window_end = channel.window_end
        reference, reference_at = channel.reference, channel.reference_at
        eligible_time = record.corrected_at
        if (
            eligible_time is not None
            and record.time_state in ("known", "replay")
            and (window_end is None or eligible_time > window_end)
        ):
            window_end = advance_window_end(window_end, eligible_time)
            reference = (
                _receive(rt, uow)
                if channel.facts.origin_mode == "REPLAY"
                else TimeFacts(normalized.state, normalized.corrected_at, normalized.uncertainty_ms)
            )
            reference_at = eligible_time
        updated = replace(
            channel,
            has_history=True,
            last_received_at=uow.now,
            last_observed_at=max(filter(None, (channel.last_observed_at, record.observed_at))),
            window_end=window_end,
            reference=reference,
            reference_at=reference_at,
        )
        rt.store.save_channel(uow, updated)
        updated = refresh(rt, uow, updated, cause="reading", status=False)
        base = reference.corrected_at if reference is not None else None
        schedule_deadline(rt, uow, updated, anchor=stamp, at=base)
        self._evaluate(record, updated)
        if self.environment is not None and before != _signature(rt, uow, updated):
            note_status_change(rt, uow, channel.facts.sensor_type_id, self.environment.environment_id)
        self.touched.setdefault(channel.facts.channel_id, (channel.facts.sensor_type_id, before))

    def _evaluate(self, record: ReadingRecord, channel: ChannelState) -> None:
        rt, uow = self.rt, self.uow
        environment: EnvironmentRow | None = self.environment
        profile = "none" if environment is None else environment.profile_id
        mission = "none" if environment is None else environment.mission_mode
        input_ref = rt.ids.derived("evi", record.reading_id)
        observed = json.dumps({"observed_at": record.observed_at.isoformat(), "state": record.time_state})
        rt.store.save_input(
            uow,
            EvaluationInputRow(
                input_ref,
                channel.facts.channel_id,
                profile,
                mission,
                channel.facts.origin_mode,
                channel.facts.dataset,
                rt.ids.derived("prt", mission, profile, channel.facts.channel_id, channel.facts.origin_mode),
                record.rev or 0,
                uow.release.release_id,
                EVALUATOR_VERSION,
                observed,
                channel.window_end,
                0,
            ),
        )
        if environment is None:
            return  # series only: never evaluated under a guessed profile
        stamp = TimeInput(
            record.observed_at,
            channel.facts.adapter_id,
            record.segment_id or "unmapped",
            record.source_clock_version or 0,
            "source_raw",
            channel.facts.origin_mode,
        )
        result = rt.need_isolation().run(
            uow,
            KernelInput(
                EVALUATOR,
                EVALUATOR_VERSION,
                input_ref,
                stamp,
                uow.release.release_id,
                rt.ids.derived("prt", mission, profile, channel.facts.channel_id, channel.facts.origin_mode),
                record.rev or 0,
                rt.ids.derived("cmp", EVALUATOR, EVALUATOR_VERSION, input_ref),
            ),
        )
        if result == "pending":
            rt.fault(EVALUATION_UNAVAILABLE, uow.now)

    def finish(self) -> IngestOutcome:
        rt, uow = self.rt, self.uow
        changed = sorted(
            {
                t
                for c, (t, before) in self.touched.items()
                if before != _signature(rt, uow, rt.store.channel(uow, c))
            }
        )
        rt.store.save_batch(
            uow,
            BatchRow(
                self.adapter_id,
                self.batch_id,
                self.accepted,
                self.duplicates,
                json.dumps([{"index": r.index, "code": r.code, "raw": r.raw} for r in self.rejected]),
                uow.now,
            ),
        )
        uow.audit(
            AuditEntry(
                rt.ids.new("aud"),
                "sensors.batch_ingested",
                1,
                uow.actor,
                {"batch": self.adapter_id + ":" + self.batch_id},
                uow.correlation_id,
                None,
                {},
            )
        )
        rejected = tuple(
            (r.index, r.code, self.messages.get(r.code) or r.code)
            for r in sorted(self.rejected, key=lambda r: r.index)
        )
        return IngestOutcome(
            self.batch_id, self.accepted, self.duplicates, tuple(self.ids), rejected, tuple(changed)
        )


def _conversion_json(conversion: Any) -> str:
    return json.dumps(
        {
            "method": conversion.method,
            "cabin_pressure_mmhg": None
            if conversion.cabin_pressure is None
            else float(conversion.cabin_pressure.value),
            "pressure_basis": conversion.pressure_basis,
            "factor": None if conversion.factor is None else float(conversion.factor),
        }
    )


def _signature(rt: Runtime, uow: UnitOfWork, channel: ChannelState | None) -> str:
    if channel is None:
        return ""
    rows = rt.store.evaluations_of(uow, channel.facts.channel_id)
    return channel.freshness + "|" + "|".join(r.profile_id + ":" + r.status for r in rows)


def ingest(
    rt: Runtime, uow: UnitOfWork, *, adapter_id: str, batch_id: str, rows: Sequence[Any]
) -> IngestOutcome:
    """One accepted row at a time, in request order. A rejected row never stops its neighbors."""
    batch = _Batch(rt, uow, adapter_id, batch_id)
    for index, row in enumerate(rows):
        batch.row(index, row)
    return batch.finish()

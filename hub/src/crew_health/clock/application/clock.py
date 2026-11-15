from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

from crew_health.clock.domain.corrections import (
    ClockState,
    instant,
    normalized,
    raw_time,
    validate_correction,
)
from crew_health.clock.domain.errors import ClockAuthorityDenied, ClockHistoryConflict, InvalidClockValue
from crew_health.clock.domain.hlc import advance, milliseconds
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import (
    ClockCorrected,
    ClockCorrection,
    ClockRegressed,
    ClockSegment,
    Hlc,
    NormalizedTime,
    TimeInput,
)
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    UnitOfWork,
)


class ClockRepository(Protocol):
    node_id: str
    incarnation_id: str

    def append_segment(self, connection: StorageSession, value: ClockSegment) -> bool: ...
    def for_sequence(
        self, connection: StorageSession, *, source_clock_id: str, sequence: int
    ) -> ClockSegment | None: ...
    def segment(self, connection: StorageSession, source: str, segment: str) -> ClockSegment | None: ...
    def previous_offset(self, connection: StorageSession, value: ClockCorrection) -> int: ...
    def append_correction(self, connection: StorageSession, value: ClockCorrection) -> None: ...
    def correction(
        self, connection: StorageSession, *, source: str, segment: str, version: int
    ) -> ClockCorrection | None: ...
    def selected_correction(
        self, connection: StorageSession, *, source: str, segment: str, raw: datetime
    ) -> ClockCorrection | None: ...
    def history(
        self, connection: StorageSession, *, after_version: int, limit: int
    ) -> tuple[ClockCorrection, ...]: ...
    def state(self, connection: StorageSession) -> ClockState | None: ...
    def set_state(self, connection: StorageSession, value: ClockState) -> None: ...
    def correction_head(self, connection: StorageSession) -> int: ...
    def hlc(self, connection: StorageSession) -> Hlc | None: ...
    def store_hlc(self, connection: StorageSession, value: Hlc) -> None: ...
    @staticmethod
    def mission_version(
        connection: StorageSession, *, at: datetime, version: str | None = None
    ) -> MissionClockVersion: ...
    @staticmethod
    def append_mission_version(connection: StorageSession, value: MissionClockVersion) -> None: ...


@dataclass(frozen=True, slots=True)
class ClockIdentity:
    node_id: str
    incarnation_id: str
    hub_source_clock_id: str | None = None


class PersistentClocks:
    def __init__(
        self,
        *,
        repository: ClockRepository,
        settings: HubSettings,
        ids: IdFactory,
        hub_source_clock_id: str | None = None,
    ) -> None:
        self.repository, self.settings, self.ids = repository, settings, ids
        self.hub_source_clock_id = hub_source_clock_id

    def _record(self, uow: UnitOfWork, *, record_type: str, record_id: str, payload: bytes) -> None:
        uow.replicate(
            ReplicationChange(
                None,
                record_type,
                record_id,
                "append",
                payload,
                (),
                uow.release.release_id,
                Audience("operator", record_id, AudienceProjectionKey("clock", "history", 1), "1"),
            )
        )
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                record_type,
                1,
                uow.actor,
                {"clock_record": record_id},
                uow.correlation_id,
                None,
                {},
            )
        )

    def open(self, uow: UnitOfWork, segment: ClockSegment) -> None:
        if uow.actor.kind not in ("operator", "adapter", "engine"):
            raise ClockAuthorityDenied()
        if self.repository.append_segment(uow.connection, segment):
            reference = self.ids.derived("segment", segment.source_clock_id, segment.segment_id)
            self._record(
                uow,
                record_type="clock.segment",
                record_id=reference,
                payload=canonical_json(
                    {
                        "source_clock_id": segment.source_clock_id,
                        "physical_clock_id": segment.physical_clock_id,
                        "segment_id": segment.segment_id,
                        "predecessor_id": segment.predecessor_id,
                        "first_sequence": segment.first_sequence,
                        "capture_offset_version": segment.capture_offset_version,
                    }
                ),
            )

    def for_sequence(
        self, snapshot: ReadSnapshot, *, source_clock_id: str, sequence: int
    ) -> ClockSegment | None:
        return self.repository.for_sequence(
            snapshot.connection, source_clock_id=source_clock_id, sequence=sequence
        )

    def apply(self, uow: UnitOfWork, correction: ClockCorrection) -> ClockCorrected:
        if (correction.source == "measured" and uow.actor.kind != "adapter") or (
            correction.source != "measured" and uow.actor.kind != "operator"
        ):
            raise ClockAuthorityDenied()
        validate_correction(
            correction, maximum_uncertainty_ms=self.settings.integer("clock.max_uncertainty_ms")
        )
        previous = self.repository.previous_offset(uow.connection, correction)
        self.repository.append_correction(uow.connection, correction)
        self._record(
            uow,
            record_type="clock.correction",
            record_id=correction.correction_id,
            payload=canonical_json(
                {
                    "correction_id": correction.correction_id,
                    "source_clock_id": correction.source_clock_id,
                    "segment_id": correction.segment_id,
                    "version": correction.version,
                    "valid_from": instant(correction.valid_from),
                    "valid_until": None
                    if correction.valid_until is None
                    else instant(correction.valid_until),
                    "offset_ms": correction.offset_ms,
                    "uncertainty_ms": correction.uncertainty_ms,
                    "recorded_at": instant(correction.recorded_at),
                    "source": correction.source,
                }
            ),
        )
        state = self.repository.state(uow.connection)
        if state is None:
            state = ClockState(uow.now, True, correction.version, -1)
        self.repository.set_state(uow.connection, replace(state, correction_version=correction.version))
        event = ClockCorrected(
            previous, correction.offset_ms, correction.recorded_at, correction.version, correction.source
        )
        uow.publish(event)
        return event

    def history(
        self, snapshot: ReadSnapshot, *, after_version: int, limit: int
    ) -> tuple[ClockCorrection, ...]:
        return self.repository.history(snapshot.connection, after_version=after_version, limit=limit)

    def normalize(self, snapshot: ReadSnapshot, value: TimeInput) -> NormalizedTime:
        unknown = NormalizedTime(value.original_at, None, None, None, "unknown")
        clock_state = self.repository.state(snapshot.connection)
        if clock_state is not None and clock_state.frozen and clock_state.minimum_revision >= 0:
            return unknown
        if self.repository.segment(snapshot.connection, value.source_clock_id, value.segment_id) is None:
            return unknown
        captured = self.repository.correction(
            snapshot.connection,
            source=value.source_clock_id,
            segment=value.segment_id,
            version=value.source_clock_version,
        )
        raw = raw_time(value, captured)
        if raw is None:
            return unknown
        selected = self.repository.selected_correction(
            snapshot.connection, source=value.source_clock_id, segment=value.segment_id, raw=raw
        )
        if selected is None:
            return unknown
        return normalized(
            value,
            raw,
            selected,
            now=snapshot.now,
            future_tolerance_ms=self.settings.integer("clock.future_tolerance_ms"),
            maximum_uncertainty_ms=self.settings.integer("clock.max_uncertainty_ms"),
        )

    def elapsed(self, snapshot: ReadSnapshot, start: TimeInput, end: TimeInput) -> timedelta | None:
        if start.origin_mode != end.origin_mode:
            return None
        first, last = self.normalize(snapshot, start), self.normalize(snapshot, end)
        if (
            first.corrected_at is None
            or last.corrected_at is None
            or first.state == "future"
            or last.state == "future"
        ):
            return None
        elapsed = last.corrected_at - first.corrected_at
        return elapsed if elapsed >= timedelta(0) else None

    def tick(self, uow: UnitOfWork) -> Hlc:
        return self._advance(uow, None)

    def observe(self, uow: UnitOfWork, remote: Hlc) -> Hlc:
        if remote.physical_ms > milliseconds(uow.now) + self.settings.integer("clock.hlc_future_ms"):
            raise InvalidClockValue()
        return self._advance(uow, remote)

    def _advance(self, uow: UnitOfWork, remote: Hlc | None) -> Hlc:
        value = advance(
            milliseconds(uow.now),
            self.repository.hlc(uow.connection),
            remote,
            node_id=self.repository.node_id,
            incarnation_id=self.repository.incarnation_id,
            logical_max=self.settings.integer("clock.hlc_logical_max"),
        )
        self.repository.store_hlc(uow.connection, value)
        return value

    def check_regression(self, uow: UnitOfWork) -> ClockRegressed | None:
        state = self.repository.state(uow.connection)
        if state is None:
            self.repository.set_state(
                uow.connection, ClockState(uow.now, True, self.repository.correction_head(uow.connection), -1)
            )
            return None
        delta = milliseconds(state.last_committed_at) - milliseconds(uow.now)
        if delta > self.settings.integer("clock.regression_tolerance_ms") and (
            not state.frozen or state.minimum_revision == -1
        ):
            self.repository.set_state(
                uow.connection, replace(state, frozen=True, minimum_revision=state.correction_version)
            )
            event = ClockRegressed(uow.now, state.last_committed_at, delta)
            uow.publish(event)
            self._record(
                uow,
                record_type="clock.regression",
                record_id=self.ids.new("regression"),
                payload=canonical_json(
                    {
                        "observed_at": instant(uow.now),
                        "last_committed_at": instant(state.last_committed_at),
                        "delta_ms": delta,
                    }
                ),
            )
            return event
        if uow.now > state.last_committed_at:
            self.repository.set_state(uow.connection, replace(state, last_committed_at=uow.now))
        return None

    def complete_sweep(self, uow: UnitOfWork, *, correction_version: int) -> None:
        if uow.actor.kind not in ("operator", "engine"):
            raise ClockAuthorityDenied()
        matching = self.repository.history(uow.connection, after_version=correction_version - 1, limit=1)
        if (
            not matching
            or matching[0].version != correction_version
            or self.hub_source_clock_id is None
            or matching[0].source_clock_id != self.hub_source_clock_id
        ):
            raise ClockHistoryConflict()
        state = self.repository.state(uow.connection)
        if (
            state is None
            or correction_version < 1
            or correction_version != state.correction_version
            or correction_version <= state.minimum_revision
        ):
            raise ClockHistoryConflict()
        self.repository.set_state(uow.connection, replace(state, frozen=False, last_committed_at=uow.now))
        self._record(
            uow,
            record_type="clock.sweep_completed",
            record_id=self.ids.derived(
                "sweep", self.repository.node_id, self.repository.incarnation_id, str(correction_version)
            ),
            payload=canonical_json({"correction_version": correction_version}),
        )

    def clearing_allowed(self, snapshot: ReadSnapshot) -> bool:
        state = self.repository.state(snapshot.connection)
        return state is not None and not state.frozen

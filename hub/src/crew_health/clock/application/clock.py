from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol

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
    def latest_segment(self, connection: StorageSession, source: str) -> ClockSegment | None: ...
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
    def hub_capture_version(self, connection: StorageSession, segment: ClockSegment) -> int: ...
    def set_hub_capture(self, connection: StorageSession, correction: ClockCorrection) -> None: ...
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
    """The hub's node, write incarnation and its own capture stream (SYSTEM 5.2: a unique source_clock_id)."""

    node_id: str
    incarnation_id: str
    hub_source_clock_id: str


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
        return self._apply(uow, correction, capture=False)

    def _apply(self, uow: UnitOfWork, correction: ClockCorrection, *, capture: bool) -> ClockCorrected:
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
        if capture:
            self.repository.set_hub_capture(uow.connection, correction)
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
        self._raise_high_water(uow)
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

    def _raise_high_water(self, uow: UnitOfWork) -> None:
        """Every stamped write moves the durable last-committed time forward; a rollback discards it with the write."""
        state = self.repository.state(uow.connection)
        if state is not None and uow.now > state.last_committed_at:
            self.repository.set_state(uow.connection, replace(state, last_committed_at=uow.now))

    def open_hub_segment(self, uow: UnitOfWork, *, process_epoch: str) -> ClockSegment:
        """SYSTEM 5.2: the hub's own clock captures its segment directly; each process start opens a new one."""
        if self.hub_source_clock_id is None:
            raise InvalidClockValue()
        repository = self.repository
        segment_id = self.ids.derived(
            "hubsegment", repository.node_id, repository.incarnation_id, process_epoch
        )
        existing = repository.segment(uow.connection, self.hub_source_clock_id, segment_id)
        if existing is not None:
            return existing
        previous = repository.latest_segment(uow.connection, self.hub_source_clock_id)
        segment = ClockSegment(
            self.hub_source_clock_id,
            self.hub_source_clock_id,
            segment_id,
            None if previous is None else previous.segment_id,
            1 if previous is None else previous.first_sequence + 1,
            # The capture revision is the correction that confirms this segment: the operator's confirmation
            # (confirm_hub_time) takes the next global version, so a stamp captured after it normalizes.
            repository.correction_head(uow.connection) + 1,
        )
        self.open(uow, segment)
        return segment

    def confirm_hub_time(
        self, uow: UnitOfWork, *, correction_id: str, offset_ms: int, uncertainty_ms: int
    ) -> ClockCorrected:
        """The crew confirms the hub's time (SYSTEM 5.2: trusted local operator): a correction of the hub's own
        current segment, effective from now, at the next global revision. The supervisor's clock sweep then lifts a
        first start or regression freeze. Only an operator may confirm, and the uncertainty is bounded as any
        correction's."""
        if self.hub_source_clock_id is None:
            raise InvalidClockValue()
        segment = self.repository.latest_segment(uow.connection, self.hub_source_clock_id)
        if segment is None:
            raise InvalidClockValue()
        if type(offset_ms) is not int:
            raise InvalidClockValue()
        try:
            raw_from = uow.now - timedelta(milliseconds=offset_ms)
        except (OverflowError, TypeError) as error:
            raise InvalidClockValue() from error
        correction = ClockCorrection(
            correction_id,
            segment.source_clock_id,
            segment.segment_id,
            self.repository.correction_head(uow.connection) + 1,
            raw_from,
            None,
            offset_ms,
            uncertainty_ms,
            uow.now,
            "operator",
        )
        return self._apply(uow, correction, capture=True)

    def capture_hub(
        self, snapshot: ReadSnapshot, *, origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    ) -> TimeInput:
        """Tag corrected UTC with the mapping explicitly confirmed for this hub incarnation.

        Retrospective normalization and other streams never change capture provenance.
        Revision zero denotes a missing calibration and normalizes to unknown.
        """
        if self.hub_source_clock_id is None:
            raise InvalidClockValue()
        segment = self.repository.latest_segment(snapshot.connection, self.hub_source_clock_id)
        if segment is None:
            raise InvalidClockValue()
        version = self.repository.hub_capture_version(snapshot.connection, segment)
        return TimeInput(
            snapshot.now, segment.source_clock_id, segment.segment_id, version, "hub_corrected", origin_mode
        )

    def pending_sweep(self, uow: UnitOfWork) -> int | None:
        """The hub correction version a sweep may complete now, or None while clearing must stay frozen."""
        state = self.repository.state(uow.connection)
        if state is None or not state.frozen or state.correction_version < 1:
            return None
        if state.correction_version <= state.minimum_revision or self.hub_source_clock_id is None:
            return None
        matching = self.repository.history(
            uow.connection, after_version=state.correction_version - 1, limit=1
        )
        if not matching or matching[0].source_clock_id != self.hub_source_clock_id:
            return None
        return state.correction_version

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

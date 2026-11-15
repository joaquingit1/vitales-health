"""Stored shapes and the storage protocol of the sensor runtime (E23 5.1).

The SQLite adapter implements `SensorStore`; the application layer sees only this. Values are exact: Decimal for
quantities, aware UTC for instants, nothing rounded and no field defaulted when it is unknown.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.sensors.domain.channel import ChannelFacts, Freshness, Origin
from crew_health.sensors.domain.freshness import ClockState, TimeFacts
from crew_health.sensors.domain.reading import QualityStatus, TimeState

type View = UnitOfWork | ReadSnapshot


@dataclass(frozen=True, slots=True)
class ChannelState:
    """One registry channel with its runtime state: freshness, E, the D14 reference and the quiet watch."""

    facts: ChannelFacts
    location: str  # SensorChannel.location, a configuration id
    input_unit: str
    trusted: bool
    health_url: str | None
    registry_version: str
    freshness: Freshness
    has_history: bool
    last_received_at: datetime | None
    last_observed_at: datetime | None
    window_end: datetime | None  # E: never moves backward
    reference_at: datetime | None  # the instant the reference belongs to (observation, or D14 receive)
    reference: TimeFacts | None  # normalized time of the reading that last strictly advanced E
    quiet_anchor_at: datetime | None  # activation quiet watch, separate from D14 evidence
    quiet_anchor_environment: str | None
    stale_since: datetime | None
    next_stale_check_at: datetime | None
    clock_state: ClockState


@dataclass(frozen=True, slots=True)
class ReadingRecord:
    reading_id: str
    channel_id: str
    adapter_id: str
    adapter_sequence: int
    batch_id: str
    value: Decimal  # wire value, four places
    unit: str
    canonical: Decimal  # unrounded canonical value, used by evaluation
    original_value: Decimal
    original_unit: str
    conversion_json: str | None
    mapping_version: str
    aggregation_kind: str
    aggregation_window: int | None
    observed_at: datetime
    window_start: datetime | None
    received_at: datetime
    origin_mode: Origin
    quality_status: QualityStatus
    time_state: TimeState
    corrected_at: datetime | None
    uncertainty_ms: int | None
    segment_id: str | None
    source_clock_version: int | None
    replay_json: str | None
    provenance_trace: str | None
    instant_conflict: bool
    body_sha256: str
    capture_release_id: str
    rev: int | None = None


@dataclass(frozen=True, slots=True)
class EvaluationInputRow:
    """The immutable manifest of one evaluation: exactly which readings (`rev` up to high_rev) it may see."""

    input_ref: str
    channel_id: str
    profile_id: str
    mission_id: str
    origin_mode: Origin
    dataset: str | None
    partition_ref: str
    high_rev: int
    capture_release_id: str
    evaluator_version: str
    observed_json: str
    window_end: datetime | None
    correction_map_version: int


@dataclass(frozen=True, slots=True)
class EvaluationRow:
    evaluation_id: str
    channel_id: str
    profile_id: str
    config_release_id: str
    input_rev: int
    high_water_at: datetime | None
    status: str
    evaluation_json: str


@dataclass(frozen=True, slots=True)
class StatusRow:
    sensor_type_id: str
    environment_id: str
    revision: int
    last_emitted_at: datetime | None
    pending_emit_at: datetime | None


@dataclass(frozen=True, slots=True)
class EnvironmentRow:
    profile_id: str
    environment_id: str
    mission_mode: Origin
    activated_at: datetime


@dataclass(frozen=True, slots=True)
class BatchRow:
    adapter_id: str
    batch_id: str
    accepted: int
    duplicates: int
    rejected_json: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class RegistryChannel:
    """The static, validated part of a channel (E23 5.3). Runtime state is kept when a channel is re-registered."""

    facts: ChannelFacts
    location: str
    input_unit: str
    trusted: bool
    health_url: str | None
    registry_version: str


@dataclass(frozen=True, slots=True)
class ScheduleFact:
    """One scheduled job as E01 holds it: its current generation, due time and whether it has completed."""

    generation: int
    due: datetime | None
    completed: bool


class SensorStore(Protocol):
    def channels(self, view: View) -> Sequence[ChannelState]: ...
    def channel(self, view: View, channel_id: str) -> ChannelState | None: ...
    def register_channel(self, uow: UnitOfWork, channel: RegistryChannel) -> None: ...
    def save_channel(self, uow: UnitOfWork, channel: ChannelState) -> None: ...
    def reading_at(self, view: View, channel_id: str, sequence: int) -> ReadingRecord | None: ...
    def reading(self, view: View, reading_id: str) -> ReadingRecord | None: ...
    def insert_reading(self, uow: UnitOfWork, reading: ReadingRecord) -> int: ...
    def readings_since(
        self, view: View, channel_id: str, *, since: datetime | None, up_to_rev: int | None, limit: int
    ) -> Sequence[ReadingRecord]: ...
    def recent_readings(
        self, view: View, channel_id: str, *, before: datetime, limit: int
    ) -> Sequence[ReadingRecord]:
        """The `limit` newest readings observed strictly before `before`, newest first (plausibility history)."""
        ...

    def newest_readings(
        self,
        view: View,
        channel_id: str,
        *,
        until: datetime | None,
        statuses: tuple[QualityStatus, ...],
        time_states: tuple[TimeState, ...],
        limit: int,
        activated_at: datetime | None = None,
    ) -> Sequence[ReadingRecord]:
        """The `limit` newest readings observed at or before `until` (any time when None) with one of `statuses`
        and `time_states`, newest first by observed time then revision. Bounded, and never an old slice. With
        `activated_at`, only readings that belong to the active place (DE23-13: REPLAY by receive time)."""
        ...

    def instant_peers(
        self, view: View, channel_id: str, corrected_at: datetime, origin: Origin, dataset: str | None
    ) -> Sequence[ReadingRecord]: ...
    def save_input(self, uow: UnitOfWork, row: EvaluationInputRow) -> None: ...
    def input(self, view: View, input_ref: str) -> EvaluationInputRow | None: ...
    def evaluation(
        self, view: View, channel_id: str, profile_id: str, release_id: str
    ) -> EvaluationRow | None: ...
    def evaluations_of(self, view: View, channel_id: str) -> Sequence[EvaluationRow]: ...
    def save_evaluation(self, uow: UnitOfWork, row: EvaluationRow) -> None: ...
    def status(self, view: View, sensor_type_id: str, environment_id: str) -> StatusRow | None: ...
    def save_status(self, uow: UnitOfWork, row: StatusRow) -> None: ...
    def save_batch(self, uow: UnitOfWork, row: BatchRow) -> None: ...
    def environment(self, view: View) -> EnvironmentRow | None: ...
    def save_environment(self, uow: UnitOfWork, row: EnvironmentRow) -> None: ...
    def schedule_of(self, view: View, key: str) -> ScheduleFact | None: ...

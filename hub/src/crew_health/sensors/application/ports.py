"""Sensor ports exactly as E23 4.2 declares them. Implemented by ticket T3, consumed by E24, E27 and E32.

Left out until R-E23-5 agrees the binding (never invented here):
- `SensorStateQuery.last_results`, whose return type `SensorEvaluated` is a bus payload export (E23 4.3);
- the `SensorEvaluated` and `ChannelFreshnessChanged` payload exports themselves, their discriminators, and the
  Quantity-bearing duration and coverage fields;
- a caller-snapshot `SensorStatus` projection for `HabitatParts` and `getHabitatView`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, Protocol

from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

OriginMode = Literal["LIVE", "REPLAY", "SIM"]
Freshness = Literal["current", "stale", "never_reported"]  # generated SensorChannel.freshness
QualityStatus = Literal["accepted", "rejected", "uncertain", "not_assessed"]


@dataclass(frozen=True, slots=True)
class HabitatStream:
    channel_id: str
    sensor_type_id: str
    origin_mode: OriginMode
    source_label: str
    dataset: str | None


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One accepted reading: observation time, canonical Quantity, quality status and origin mode."""

    observed_at: datetime
    value: Quantity
    quality_status: QualityStatus
    origin_mode: OriginMode


@dataclass(frozen=True, slots=True)
class SeriesSlice:
    points: tuple[SeriesPoint, ...]
    truncated: bool


class SensorStateQuery(Protocol):
    def freshness(self, uow: UnitOfWork, channel_id: str) -> Freshness: ...  # SensorChannel.freshness
    def channels(self, uow: UnitOfWork) -> Sequence[str]: ...
    def type_name(self, uow: UnitOfWork, sensor_type_id: str) -> str: ...


class SensorSeriesReader(Protocol):
    def accepted(self, channel_id: str, *, until: datetime, limit: int) -> SeriesSlice: ...


class ProvenanceStreamsPort(Protocol):
    def habitat_streams(self, snapshot: ReadSnapshot) -> Sequence[HabitatStream]: ...


class FreshnessSweeper(Protocol):
    """One tick of the independent freshness sweep, as its own unit of work (E23 3.4). The timer, the startup
    sweep and operator tooling call it; it needs no HTTP identity and no session."""

    def sweep_once(self) -> int: ...  # the number of channels it looked at


# Bus payloads for E24 (E23 4.3, R-E24-14), built in the owner's place under AGENTS 1 rule 9. Every number is
# formatted here by E23 (decimals and trim from the catalog); E24 formats no sensor value.
@dataclass(frozen=True, slots=True)
class LimitOutcome:
    limit_id: str
    crossed: bool | None
    clear_holds: bool | None
    value: Decimal | None
    value_unit: str | None  # the canonical UCUM code; None when the release's unit mapping names none
    coverage: Decimal | None
    aggregation_id: str
    aggregation_label: str
    window_seconds: int | None
    value_text: str
    unit_display: str
    limit_value_text: str


@dataclass(frozen=True, slots=True)
class SensorEvaluated:
    event_type: ClassVar[str] = "sensors.evaluated"
    config_release_id: str
    sensor_type_id: str
    channel_id: str
    location: str | None
    origin_mode: OriginMode
    reading_id: str
    window_end: datetime
    observed_original_at: datetime
    observed_corrected_at: datetime | None
    observed_uncertainty_ms: int | None
    observed_state: Literal["known", "unknown", "future", "replay"]  # internal enum
    source_clock_id: str
    segment_id: str
    source_clock_version: int
    receive_reference: datetime | None
    clock_state: Literal["trusted", "uncertain", "unknown"]  # internal enum
    freshness: Literal["current", "stale"]  # internal enum
    stale_after_seconds: int
    results: tuple[LimitOutcome, ...]
    sensor_name: str
    location_label: str | None
    source_label: str
    last_received_time: str | None
    replay_dataset: str | None


@dataclass(frozen=True, slots=True)
class ChannelFreshnessChanged:
    event_type: ClassVar[str] = "sensors.freshness_changed"
    config_release_id: str
    channel_id: str
    sensor_type_id: str
    state: Freshness
    stale_since: datetime | None
    last_reading_id: str | None
    origin_mode: OriginMode
    sensor_name: str
    location_label: str | None
    source_label: str
    replay_dataset: str | None
    observed_original_at: datetime | None
    observed_corrected_at: datetime | None
    observed_uncertainty_ms: int | None
    observed_state: Literal["known", "unknown", "future", "replay"] | None  # internal enum
    source_clock_id: str | None
    segment_id: str | None
    source_clock_version: int | None
    receive_reference: datetime | None

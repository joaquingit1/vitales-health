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
from typing import Literal, Protocol

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

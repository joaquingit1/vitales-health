"""Catalog and channel facts. Pure values read from validated release documents (E23 3.7, 5.3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Origin = Literal["LIVE", "REPLAY", "SIM"]
Freshness = Literal["current", "stale", "never_reported"]
Level = Literal["info", "watch", "act", "emergency"]
Comparator = Literal["above", "at_or_above", "below", "at_or_below"]
AggregationKind = Literal["latest", "mean", "time_weighted_mean", "energy_mean", "max", "sum"]
Conversion = Literal["identity", "scale", "ppm_to_mmhg"]

LEVEL_ORDER: tuple[Level, ...] = ("info", "watch", "act", "emergency")  # CONTRACTS 11 fixed vocabulary


def level_rank(level: Level) -> int:
    return LEVEL_ORDER.index(level)


@dataclass(frozen=True, slots=True)
class AcceptedUnit:
    unit: str
    conversion: Conversion
    factor: Decimal | None


@dataclass(frozen=True, slots=True)
class AggregationSpec:
    id: str
    kind: AggregationKind
    window_seconds: int | None
    label: str


@dataclass(frozen=True, slots=True)
class DisplaySpec:
    range: tuple[Decimal, Decimal]
    by_environment: tuple[tuple[str, tuple[Decimal, Decimal]], ...]
    basis: str

    def range_for(self, environment_id: str) -> tuple[Decimal, Decimal]:
        for key, value in self.by_environment:
            if key == environment_id:
                return value
        return self.range


@dataclass(frozen=True, slots=True)
class SensorTypeFacts:
    id: str
    hazard_id: str
    name: str
    long_name: str
    body_region: str | None
    unit: str
    unit_display: str
    decimals: int
    trim_trailing_zeros: bool
    accepted_units: tuple[AcceptedUnit, ...]
    aggregations: tuple[AggregationSpec, ...]
    display_aggregation_id: str
    display: DisplaySpec
    expected_interval_seconds: int | None
    interval_basis: str
    interval_source_ids: tuple[str, ...]
    stale_after_seconds: int
    stale_basis: str
    no_reading_label: str
    manual_alternative_item_id: str | None
    citation_text: str

    def aggregation(self, aggregation_id: str) -> AggregationSpec | None:
        for spec in self.aggregations:
            if spec.id == aggregation_id:
                return spec
        return None

    def accepted_unit(self, unit: str) -> AcceptedUnit | None:
        for accepted in self.accepted_units:
            if accepted.unit == unit:
                return accepted
        return None


@dataclass(frozen=True, slots=True)
class ChannelFacts:
    channel_id: str
    sensor_type_id: str
    hazard_id: str
    adapter_id: str
    origin_mode: Origin
    location_label: str | None
    source_label: str
    dataset: str | None
    display_note: str | None
    expected_interval_seconds: int | None
    stale_after_seconds: int
    environment_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegistrationPolicy:
    """What a channel of an origin may do while plausibility criteria are missing (R-E23-2)."""

    register: bool
    capture: bool
    evaluate: bool
    reason: str

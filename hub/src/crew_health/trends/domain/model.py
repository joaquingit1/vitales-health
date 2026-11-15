"""Pure E14 values, independent of presentation and persistence DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.kernel.clock import TimeInput
from crew_health.kernel.units import Quantity

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]
type AcquisitionMethod = Literal[
    "self_report",
    "manual_instrument",
    "device",
    "camera_estimate",
    "crewmate_palpation",
    "imported_report",
    "clinician_entry",
    "derived",
]
type QualityStatus = Literal["accepted", "rejected", "uncertain", "not_assessed"]
type TimeState = Literal["known", "unknown", "future", "replay"]
type SummaryState = Literal["within_usual", "below_usual", "above_usual", "no_baseline", "no_reading"]
type ExclusionReason = Literal[
    "current_reading",
    "superseded",
    "outside_window",
    "cohort_mismatch",
    "episode_linked",
    "episode_period",
    "time_unknown",
    "future_time",
    "not_accepted",
    "unit_mismatch",
    "other_partition",
    "outside_mission_clock",
]


@dataclass(frozen=True, slots=True)
class Sample:
    observation_id: str
    patient_id: str
    operator_id: str
    mission_id: str
    episode_id: str | None
    origin_mode: OriginMode
    code: str
    quantity: Quantity
    quality_status: QualityStatus
    acquisition_method: AcquisitionMethod
    device_id: str | None
    context: tuple[tuple[str, str], ...]
    supersedes: str | None
    observed: TimeInput
    observed_at: datetime | None
    time_state: TimeState
    mission_day: int | None
    superseded_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Partition:
    person_id: str
    mission_id: str
    origin_mode: OriginMode


@dataclass(frozen=True, slots=True)
class Comparison:
    """The catalog's within_usual band for one indicator, with its provenance."""

    band: Decimal  # largest difference from the usual value still within usual, at display precision; 0 is exact
    basis: Literal["sourced", "team default"]
    source_ids: tuple[str, ...]  # sources that give this band; empty while unsourced
    nasa_checked: tuple[str, ...]  # NASA catalog IDs read before the band was set
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class BaselinePolicy:
    method: Literal["median", "mean"]
    window_days: int
    min_samples: int
    basis: str
    catalog_version: str
    decimals: int
    comparison: Comparison


@dataclass(frozen=True, slots=True)
class Period:
    start: datetime
    end: datetime | None


@dataclass(frozen=True, slots=True)
class Exclusion:
    observation_id: str
    reason: ExclusionReason
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Selection:
    samples: tuple[Sample, ...]
    exclusions: tuple[Exclusion, ...]


@dataclass(frozen=True, slots=True)
class BaselineSummary:
    value: Quantity
    method: Literal["median", "mean"]
    window_days: int
    min_samples: int
    sample_count: int
    included_ids: tuple[str, ...]
    exclusions: tuple[Exclusion, ...]
    cohort_policy: str
    exclusion_policy: str
    catalog_version: str
    basis: str
    comparison: Comparison


@dataclass(frozen=True, slots=True)
class InsufficientBaseline:
    sample_count: int
    min_samples: int
    window_days: int
    included_ids: tuple[str, ...]
    exclusions: tuple[Exclusion, ...]
    cohort_policy: str
    exclusion_policy: str
    catalog_version: str


@dataclass(frozen=True, slots=True)
class TrendPoint:
    mission_day: int
    value: Quantity | None
    observation_id: str | None
    readings_that_day: int


@dataclass(frozen=True, slots=True)
class DailyValue:
    mission_day: int
    value: Quantity


@dataclass(frozen=True, slots=True)
class Unavailable:
    provider: str
    reason: str

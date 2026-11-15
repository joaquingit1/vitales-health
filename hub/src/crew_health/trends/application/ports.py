"""E14 proposal for X-E03-16.

E03 composes labels, delta_text and source_label through its own copy using
TemplateRenderer. E03 requests pvt_lapses for pvt_median extras, and energy and
stress for mood extras.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from crew_health.kernel.clock import NormalizedTime, TimeInput
from crew_health.kernel.units import ConvertedQuantity, Quantity
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

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
type SummaryState = Literal[
    "within_usual", "below_usual", "above_usual", "no_baseline", "no_reading"
]  # = how-you-are-view.schema.json state enum
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
class Unavailable:
    provider: str  # "trends", "E01", "E15", "E23", "forecast_worker", "task_runner"
    reason: str  # stable snake_case code, never free text


# Provided by E01 (crew_health.records); structural copy of SYSTEM 4.2 ObservationReader with the R-E01-17
# accepted(...) signature (docs/requests/E01.md:218) until records exports it (DECISIONS A9). The row is a read only
# protocol whose members equal records' ObservationRecord field for field, so RecordsQueries binds with no adapter.
# Provider contract: every filter is applied before the limit, so a page shorter than `limit` is the whole history.
class ObservationRef(Protocol):
    @property
    def observation_id(self) -> str: ...
    @property
    def patient_id(self) -> str: ...
    @property
    def operator_id(self) -> str: ...
    @property
    def mission_id(self) -> str: ...
    @property
    def episode_id(self) -> str | None: ...
    @property
    def code(self) -> str: ...
    @property
    def quantity(self) -> Quantity: ...
    @property
    def conversion(self) -> ConvertedQuantity: ...
    @property
    def observed(self) -> TimeInput: ...
    @property
    def normalized(
        self,
    ) -> NormalizedTime: ...  # corrected_at is the observation time (D3); None when unknown
    @property
    def entered_at(self) -> datetime: ...
    @property
    def received_at(self) -> datetime: ...
    @property
    def origin_mode(self) -> OriginMode: ...
    @property
    def acquisition_method(self) -> AcquisitionMethod: ...
    @property
    def quality_status(self) -> QualityStatus: ...
    @property
    def quality_rule_version(self) -> str: ...
    @property
    def quality_reasons(self) -> tuple[str, ...]: ...
    @property
    def device_id(self) -> str | None: ...
    @property
    def context(self) -> tuple[tuple[str, str], ...]: ...
    @property
    def body_region(self) -> str | None: ...
    @property
    def supersedes(self) -> str | None: ...
    @property
    def release_id(self) -> str: ...
    @property
    def proof_ref(self) -> str | None: ...
    @property
    def measurement_request_id(self) -> str | None: ...
    @property
    def care_basis_ref(self) -> str: ...


class ObservationReader(Protocol):
    def accepted(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        code: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        limit: int,
    ) -> tuple[ObservationRef, ...]: ...


# Consumer-owned protocol, to be implemented by E15 (crew_health.episodes): unresolved episode periods (PLAN R1).
@dataclass(frozen=True, slots=True)
class ExclusionPeriod:
    episode_id: str
    start: datetime
    end: datetime | None  # None: unresolved, still open


@dataclass(frozen=True, slots=True)
class ExclusionPeriods:
    policy_version: str
    periods: tuple[ExclusionPeriod, ...]
    complete: bool


class EpisodeExclusionReader(Protocol):
    def periods(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        until: datetime,
        limit: int,
    ) -> ExclusionPeriods: ...


# Provided by E23 (crew_health.sensors); structural copy of E23 4.2 (docs/tdd/E23.md:475-497) until it is on main.
@dataclass(frozen=True, slots=True)
class SeriesPoint:
    observed_at: datetime
    value: Quantity
    quality_status: QualityStatus
    origin_mode: OriginMode


@dataclass(frozen=True, slots=True)
class SeriesSlice:
    points: tuple[SeriesPoint, ...]
    truncated: bool


class SensorSeriesReader(Protocol):
    def accepted(self, channel_id: str, *, until: datetime, limit: int) -> SeriesSlice: ...


# Consumer-owned protocol, implemented by trends.adapters.worker_client against workers/forecast (E14 T2).
@dataclass(frozen=True, slots=True)
class DailyValue:
    mission_day: int
    value: Quantity


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    request_id: str
    policy_version: str | None
    series: tuple[DailyValue, ...]


@dataclass(frozen=True, slots=True)
class ForecastStep:
    mission_day: int
    low: Quantity
    median: Quantity
    high: Quantity


@dataclass(frozen=True, slots=True)
class WorkerForecast:
    model_id: str
    model_revision: str
    model_sha256: str
    policy_version: str
    interval_level: Decimal
    steps: tuple[ForecastStep, ...]


@dataclass(frozen=True, slots=True)
class WorkerUnavailable:
    reason: str


class ForecastWorkerPort(Protocol):
    def forecast(self, request: ForecastRequest) -> WorkerForecast | WorkerUnavailable: ...


# PROVIDED by E14 (SYSTEM 4.4, X-E03-16): proposal, consumed by E03 HowYouAreView.
@dataclass(frozen=True, slots=True)
class SummaryRequest:
    person_id: str
    mission_id: str
    origin_mode: OriginMode
    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadingRef:
    observation_id: str
    value: Quantity
    observed_at: datetime
    mission_day: int
    acquisition_method: AcquisitionMethod
    device_id: str | None
    operator_id: str
    superseded_ids: tuple[str, ...]  # lineage: the readings this one replaced (correction over band estimate)


@dataclass(frozen=True, slots=True)
class Exclusion:
    observation_id: str
    reason: ExclusionReason


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """How within_usual was decided: the catalog baseline's within_usual band with its provenance."""

    band: Quantity  # largest difference from the usual value still within usual, at display precision; 0 is exact
    basis: Literal["sourced", "team default"]  # common.schema.json basis
    source_ids: tuple[str, ...]  # sources that give this band; empty while unsourced
    nasa_checked: tuple[str, ...]  # NASA catalog IDs read before the band was set
    to_be_set_by: str | None  # common.schema.json to_be_set_by; None when final for the release


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
    comparison: ComparisonPolicy


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
class IndicatorSummary:
    code: str
    origin_mode: OriginMode
    state: SummaryState
    today: ReadingRef | None  # only a reading from the current mission day
    latest: ReadingRef | None  # the latest reading with its own time, never shown as today
    usual: BaselineSummary | InsufficientBaseline | Unavailable
    delta: Quantity | None  # today minus usual, same unit; None unless both exist
    trend: tuple[TrendPoint, ...] | Unavailable
    methods: tuple[
        AcquisitionMethod, ...
    ]  # sorted unique methods of today and the trend points (source_label input)
    decimals: int  # catalog display precision used for the state comparison


@dataclass(frozen=True, slots=True)
class IndicatorUnavailable:
    code: str
    origin_mode: OriginMode
    unavailable: Unavailable


class IndicatorSummaryPort(Protocol):
    def summaries(
        self, snapshot: ReadSnapshot, session: SessionContext, request: SummaryRequest
    ) -> tuple[IndicatorSummary | IndicatorUnavailable, ...]: ...


# PROVIDED by E14: environment series as place context (E14-02).
@dataclass(frozen=True, slots=True)
class EnvironmentPoint:
    observed_at: datetime
    value: Quantity
    quality_status: QualityStatus
    origin_mode: OriginMode


@dataclass(frozen=True, slots=True)
class EnvironmentSeries:
    channel_id: str
    origin_mode: OriginMode | Literal["mixed"]
    points: tuple[EnvironmentPoint, ...]
    personal_exposure: Literal[False]  # always False: habitat series never become a person's exposure
    place_context_only: Literal[True]
    original_dates_kept: Literal[True]
    pressure_basis: Unavailable  # SeriesSlice carries no pressure basis today (R1): stated, never assumed


class EnvironmentContextPort(Protocol):
    def series(
        self, snapshot: ReadSnapshot, session: SessionContext, channel_id: str, *, until: datetime
    ) -> EnvironmentSeries | Unavailable: ...


# PROVIDED by E14: experimental forecast (E14-03). Never a measurement, never a safety input.
@dataclass(frozen=True, slots=True)
class SeriesKey:
    person_id: str
    mission_id: str
    origin_mode: OriginMode
    code: str


@dataclass(frozen=True, slots=True)
class ForecastTicket:
    input_ref: str
    generation: int
    task_id: str
    reconnected: bool


@dataclass(frozen=True, slots=True)
class ForecastView:
    kind: Literal["experimental_forecast"]
    experimental: Literal[True]
    not_a_measurement: Literal[True]
    input_ref: str
    input_observation_ids: tuple[str, ...]
    input_days: tuple[int, ...]
    model_id: str
    model_revision: str
    model_sha256: str
    policy_version: str
    interval_level: Decimal
    steps: tuple[ForecastStep, ...]
    created_at: datetime


class ForecastCommandsPort(Protocol):
    def refresh(
        self, uow: UnitOfWork, session: SessionContext, key: SeriesKey
    ) -> ForecastTicket | Unavailable: ...


class ForecastQueriesPort(Protocol):
    def latest(
        self, snapshot: ReadSnapshot, session: SessionContext, key: SeriesKey
    ) -> ForecastView | Unavailable: ...

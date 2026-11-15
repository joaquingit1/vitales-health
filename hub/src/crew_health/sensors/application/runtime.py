"""What the sensor application needs from the hub, bound once in pass 2 (E23 4.1, SYSTEM 5.12).

Required providers are E01's own (storage, ids, settings, scheduler through the unit of work, the evaluation
isolation and the unit converter). Optional ones are other modules' ports: with one absent the matching feature
says so and nothing is invented (AGENTS 1 rule 2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import ClockCorrections, ClockSegments
from crew_health.kernel.config import ConfigReleaseReader, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationIsolation
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import WireUnitConverter
from crew_health.kernel.uow import UnitOfWork
from crew_health.mission.application.ports import MissionStatePort
from crew_health.sensors.application.store import SensorStore
from crew_health.sensors.domain.config import SensorConfig

EVALUATOR = "sensors.evaluate"
EVALUATOR_VERSION = "1"
FRESHNESS_HANDLER = "sensors.freshness"
STATUS_HANDLER = "sensors.status_emit"
HEALTH_COMPONENT = "sensors"
# The five internal fault codes of the `sensors` health component (E23 3.4). Public Health shows only degraded.
EVALUATION_UNAVAILABLE = "EVALUATION_UNAVAILABLE"
SCHEDULER_LAG = "SCHEDULER_LAG"
INSTANT_CONFLICT = "INSTANT_CONFLICT"
SEQUENCE_CONFLICT = "SEQUENCE_CONFLICT"
WINDOW_SCAN_BOUND = "WINDOW_SCAN_BOUND"
FAULT_CODES = frozenset(
    {EVALUATION_UNAVAILABLE, SCHEDULER_LAG, INSTANT_CONFLICT, SEQUENCE_CONFLICT, WINDOW_SCAN_BOUND}
)


@dataclass(frozen=True, slots=True)
class Limits:
    """The section 11 hub settings this module reads, as plain integers."""

    batch_max_readings: int
    window_scan_max_readings: int
    series_max_points: int
    page_max: int
    max_channels: int
    freshness_sweep_s: int
    scheduler_lag_max_s: int
    sweep_page: int
    raw_row_max_bytes: int
    registry_max_bytes: int
    aggregate_bucket_s: int
    plausibility_history_max: int

    @classmethod
    def of(cls, settings: HubSettings) -> Limits:
        return cls(
            settings.integer("sensors.batch_max_readings"),
            settings.integer("sensors.window_scan_max_readings"),
            settings.integer("sensors.series_max_points"),
            settings.integer("sensors.page_max"),
            settings.integer("sensors.max_channels"),
            settings.integer("sensors.freshness_sweep_s"),
            settings.integer("sensors.scheduler_lag_max_s"),
            settings.integer("sensors.sweep_page"),
            settings.integer("sensors.raw_row_max_bytes"),
            settings.integer("commands.max_bytes"),
            settings.integer("sensors.aggregate_bucket_s"),
            settings.integer("sensors.plausibility_history_max"),
        )


class Faults:
    """The `sensors` component of S5.10 health: degraded with the first of its active codes, or ok."""

    def __init__(self, health: HealthState) -> None:
        self._health = health
        self.active: set[str] = set()

    def mark(self, code: str, since: datetime) -> None:
        if code not in FAULT_CODES:
            raise ValueError("not one of the five sensors fault codes")
        self.active.add(code)
        self._health.mark_degraded(HEALTH_COMPONENT, sorted(self.active)[0], since)

    def clear(self, code: str, since: datetime) -> None:
        self.active.discard(code)
        if self.active:
            self._health.mark_degraded(HEALTH_COMPONENT, sorted(self.active)[0], since)
        else:
            self._health.mark_ok(HEALTH_COMPONENT)


@dataclass(slots=True)
class Runtime:
    store: SensorStore
    ids: IdFactory
    limits: Limits
    config_of: Callable[[ReleaseHandle], SensorConfig]
    messages_of: Callable[[ReleaseHandle], Mapping[str, str]]
    hub_source_clock_id: str
    mission: MissionStatePort | None = None
    corrections: ClockCorrections | None = None
    segments: ClockSegments | None = None
    converter: WireUnitConverter | None = None
    isolation: EvaluationIsolation | None = None
    faults: Faults | None = None
    release_reader: ConfigReleaseReader | None = None
    read_file: Callable[[str], bytes] | None = None
    deployment_profile: str = ""
    demo_registry_path: str = ""

    def need_converter(self) -> WireUnitConverter:
        if self.converter is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "unit_converter_unavailable", retryable=True)
        return self.converter

    def need_isolation(self) -> EvaluationIsolation:
        if self.isolation is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "evaluation_isolation_unavailable", retryable=True)
        return self.isolation

    def fault(self, code: str, since: datetime) -> None:
        if self.faults is not None:
            self.faults.mark(code, since)

    def release_of(self, uow: UnitOfWork, release_id: str) -> ReleaseHandle:
        """The release an input was captured under: the unit's own, or the pinned one by id."""
        if uow.release.release_id == release_id:
            return uow.release
        if self.release_reader is None:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "release_reader_unavailable", retryable=True)
        return self.release_reader.release(release_id)

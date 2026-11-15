"""Mission ports exactly as E23 4.2 declares them (docs/tdd/E23.md:424).

Left out until R-E23-5 agrees the binding (never invented here):
- `MissionStatePort.latest_event`, `events_since` and `state_facts`: their return types `MissionEventRecorded`
  and `StateFactChanged` are bus payload exports (E23 4.3), held with the discriminators and the S5.2 eligibility
  binding;
- `EnvironmentFacts.applicable`: its return type `Fact[bool]` is an E27-owned type that has no application export;
- the bus payloads `EnvironmentActivated`, `OverlayChanged`, `MissionEventRecorded` and `StateFactChanged`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.contracts_gen.schemas.environment_profile_schema import (
    EnvironmentProfile as EnvironmentProfileModel,
)
from crew_health.contracts_gen.schemas.sensor_status_schema import SensorStatus as SensorStatusModel
from crew_health.contracts_gen.schemas.today_view_schema import Environment as TodayEnvironment
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

OriginMode = Literal["LIVE", "REPLAY", "SIM"]


@dataclass(frozen=True, slots=True)
class MissionFacts:
    mission_id: str
    mission_mode: OriginMode  # MissionContext.mission_mode
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


@dataclass(frozen=True, slots=True)
class OverlayFact:
    overlay_id: str
    person_id: str
    kind: str  # ActivityOverlay.overlay
    state: str  # ActivityOverlay.state
    planned_start: datetime | None
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReturnOption:
    trip_text: str
    source_ids: tuple[str, ...]
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class PlaceFact:  # habitat facts only, never a person's
    kind: str
    text: str
    at: datetime
    origin_mode: OriginMode
    source_label: str


type Snap = UnitOfWork | ReadSnapshot


class MissionStatePort(Protocol):
    def current(self, uow: Snap | None = None) -> MissionFacts | None: ...  # None: opens its own snapshot
    def mission_mode(self, uow: Snap | None = None) -> OriginMode | None: ...
    def active_environment_id(self, uow: Snap | None = None) -> str | None: ...
    def overlays(self, uow: UnitOfWork, person_id: str, since: datetime) -> Sequence[OverlayFact]: ...
    def return_option(self, uow: Snap | None, environment_id: str) -> ReturnOption | None: ...
    def day_end(self, instant: datetime) -> datetime: ...


class OverlayPort(Protocol):
    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]: ...
    def intervals(
        self, person_id: str, kinds: Collection[str], since: datetime, until: datetime, limit: int
    ) -> Sequence[OverlayFact]: ...


class EnvironmentFacts(Protocol):
    def place_facts(self, uow: Snap, start: datetime, end: datetime) -> Sequence[PlaceFact]: ...


class LocationPort(Protocol):
    def location_of(self, person_id: str, at: datetime) -> str | None: ...


class DayLabelPort(Protocol):
    def inline_label_at(self, instant: datetime) -> str: ...


class ActivationParticipant(Protocol):  # declared in mission, implemented by sensors
    def on_activated(self, uow: UnitOfWork, profile: EnvironmentProfileModel) -> None: ...


class HabitatParts(Protocol):  # E03 Today
    def today_part(self, snapshot: ReadSnapshot, at: datetime) -> TodayEnvironment: ...
    def today_sensors(self, snapshot: ReadSnapshot, at: datetime) -> Sequence[SensorStatusModel]: ...


# Provided by E02 (crew_health.identity); structural copy of E02 section 2 until it is on main (same shape as
# the copies in help and measurement). Absent roster: no person ID can be named by a mission event (fail closed).
class RosterPort(Protocol):
    def person_ids(self) -> frozenset[str]: ...


class MissionInitializer(Protocol):
    """UC-M0 for an operator surface (the CLI of E23 5, UC-M0): one unit of work, one initialization, ever."""

    def initialize(
        self,
        *,
        operator_id: str,
        mission_mode: OriginMode,
        planned_length_days: int | None,
        site_node_id: str,
        environment_id: str,
    ) -> str: ...  # returns the new mission_id

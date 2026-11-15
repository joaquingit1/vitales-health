from __future__ import annotations

from dataclasses import dataclass

from crew_health.kernel.clock import Clock, ClockCorrections
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.routine.application.ports import (
    ClockTrust,
    HubClock,
    LocationPort,
    MissionStatePort,
    RecordFacts,
    RosterPort,
    Visibility,
    WarningCommands,
)
from crew_health.routine.application.settings import RoutineSettings
from crew_health.routine.application.store_port import ReleaseSource, RoutineStore


@dataclass(frozen=True, slots=True)
class RoutineDeps:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    clock: Clock
    mission_clock: MissionClock
    ids: IdFactory
    settings: RoutineSettings
    idempotency: IdempotencyStore
    receipts: CommandReceipts | None
    health: HealthState
    releases: ReleaseSource
    store: RoutineStore
    mission: MissionStatePort | None
    roster: RosterPort | None
    locations: LocationPort | None
    clock_trust: ClockTrust | None
    corrections: ClockCorrections
    hub_clock: HubClock | None
    visibility: Visibility | None = None
    records: RecordFacts | None = None
    warnings: WarningCommands | None = None

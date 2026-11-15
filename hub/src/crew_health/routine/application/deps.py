from dataclasses import dataclass

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.routine.application.ports import (
    LocationPort,
    MissionStatePort,
    ReleaseSource,
    RosterPort,
    RoutineStore,
)
from crew_health.routine.application.settings import RoutineSettings


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

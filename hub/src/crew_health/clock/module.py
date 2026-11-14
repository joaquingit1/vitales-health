from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import ClockIdentity, PersistentClocks
from crew_health.clock.application.history import MissionClockService
from crew_health.clock.settings import specifications
from crew_health.kernel.clock import ClockCorrections, ClockSegments, HlcClock
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshotFactory


def _clocks(resolver: Resolver) -> PersistentClocks:
    settings = resolver.require(HubSettings)  # type: ignore[type-abstract]
    identity = resolver.require(ClockIdentity)
    repository = SqlClockRepository(
        settings=settings, node_id=identity.node_id, incarnation_id=identity.incarnation_id
    )
    return PersistentClocks(
        repository=repository,
        hub_source_clock_id=identity.hub_source_clock_id,
        settings=settings,
        ids=resolver.require(IdFactory),  # type: ignore[type-abstract]
    )


def register(registry: ModuleRegistry) -> None:
    for spec in specifications():
        registry.setting(spec)
    registry.provide(PersistentClocks, _clocks, requires=(HubSettings, IdFactory, ClockIdentity))
    registry.provide(
        ClockCorrections,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(PersistentClocks),
        requires=(PersistentClocks,),
    )
    registry.provide(
        ClockSegments,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(PersistentClocks),
        requires=(PersistentClocks,),
    )
    registry.provide(
        HlcClock,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(PersistentClocks),
        requires=(PersistentClocks,),
    )
    registry.provide(
        MissionClock,  # type: ignore[type-abstract]
        lambda resolver: MissionClockService(
            snapshots=resolver.require(ReadSnapshotFactory),  # type: ignore[type-abstract]
            repository=resolver.require(PersistentClocks).repository,
        ),
        requires=(ReadSnapshotFactory, PersistentClocks),
    )

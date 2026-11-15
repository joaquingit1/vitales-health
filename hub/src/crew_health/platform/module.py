from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.kernel.commands import CommandIntents, CommandReceipts, IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import HealthState, Scheduler
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import DomainEventBus, ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.platform.adapters.fingerprint import KeyedCommandFingerprint
from crew_health.platform.adapters.intents import IntentRepository
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.adapters.units import UcumUnitRegistry
from crew_health.platform.application.health import ComponentHealthState
from crew_health.platform.settings import specifications
from crew_health.platform.storage import SQLiteDatabase


def _ledger(resolver: Resolver) -> CommandLedger:
    return CommandLedger(resolver.require(IdFactory), settings=resolver.require(HubSettings))  # type: ignore[type-abstract]


def register(registry: ModuleRegistry) -> None:
    registry.provide(
        UnitOfWorkFactory,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(SQLiteDatabase),
        requires=(SQLiteDatabase,),
    )
    registry.provide(
        ReadSnapshotFactory,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(SQLiteDatabase),
        requires=(SQLiteDatabase,),
    )
    registry.provide(CommandLedger, _ledger, requires=(IdFactory, HubSettings))
    registry.provide(
        IdempotencyStore,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(CommandLedger),
        requires=(CommandLedger,),
    )
    registry.provide(
        CommandReceipts,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(CommandLedger),
        requires=(CommandLedger,),
    )
    registry.provide(
        HealthState,  # type: ignore[type-abstract]
        lambda resolver: ComponentHealthState(
            maximum=resolver.require(HubSettings).integer("registry.max_hooks")  # type: ignore[type-abstract]
        ),
        requires=(HubSettings,),
    )
    registry.provide(
        CommandIntents,  # type: ignore[type-abstract]
        lambda resolver: IntentRepository(resolver.require(CommandLedger)),
        requires=(CommandLedger,),
    )
    registry.provide(
        Scheduler,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(SQLiteDatabase).scheduler,
        requires=(SQLiteDatabase,),
    )
    registry.provide(
        DomainEventBus,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(SQLiteDatabase).bus,
        requires=(SQLiteDatabase,),
    )
    registry.provide(
        UnitRegistry,  # type: ignore[type-abstract]
        lambda resolver: UcumUnitRegistry(
            max_cached=resolver.require(HubSettings).integer("units.max_cached")  # type: ignore[type-abstract]
        ),
        requires=(HubSettings,),
    )
    registry.provide(
        KeyedCommandFingerprint,
        lambda resolver: KeyedCommandFingerprint.beside(resolver.require(SQLiteDatabase).path),
        requires=(SQLiteDatabase,),
    )
    for spec in specifications():
        registry.setting(spec)
    registry.id_prefix(record_type="command_receipt", prefix="receipt")

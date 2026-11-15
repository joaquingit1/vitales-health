from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import APIRouter

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.events.adapters.notifier import BoundedNotifier
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.api.router import create_router
from crew_health.events.application.delivery import EventDelivery, SessionAuthorizer
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.events.settings import specifications
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    AudienceRule,
    LocalProjectionRegistration,
    OutboxSubscriptions,
    ReadSnapshotFactory,
    UnitOfWorkFactory,
)


@dataclass(frozen=True, slots=True)
class EventAccess:
    authorizer: SessionAuthorizer
    projections: tuple[LocalProjectionRegistration, ...]
    audiences: Mapping[str, AudienceRule]


def _repository(resolver: Resolver) -> EventRepository:
    return EventRepository(
        settings=resolver.require(HubSettings),  # type: ignore[type-abstract]
        ids=resolver.require(IdFactory),  # type: ignore[type-abstract]
        validator=resolver.require(EventValidator),
    )


def _delivery(resolver: Resolver) -> EventDelivery:
    access = resolver.require(EventAccess)
    return EventDelivery(
        repository=resolver.require(EventRepository),
        settings=resolver.require(HubSettings),  # type: ignore[type-abstract]
        snapshots=resolver.require(ReadSnapshotFactory),  # type: ignore[type-abstract]
        authorizer=access.authorizer,
        projections=access.projections,
        audiences=access.audiences,
    )


def _notifier(resolver: Resolver) -> BoundedNotifier:
    return BoundedNotifier(settings=resolver.require(HubSettings), ids=resolver.require(IdFactory))  # type: ignore[type-abstract]


def _subscriptions(resolver: Resolver) -> DurableSubscriptions:
    return DurableSubscriptions(
        repository=resolver.require(EventRepository),
        settings=resolver.require(HubSettings),  # type: ignore[type-abstract]
        factory=resolver.require(UnitOfWorkFactory),  # type: ignore[type-abstract]
    )


def register(registry: ModuleRegistry) -> None:
    for spec in specifications():
        registry.setting(spec)
    registry.provide(EventRepository, _repository, requires=(HubSettings, IdFactory, EventValidator))
    registry.provide(
        EventDelivery, _delivery, requires=(EventRepository, HubSettings, ReadSnapshotFactory, EventAccess)
    )
    registry.provide(BoundedNotifier, _notifier, requires=(HubSettings, IdFactory))
    registry.provide(
        DurableSubscriptions, _subscriptions, requires=(EventRepository, HubSettings, UnitOfWorkFactory)
    )
    registry.provide(
        OutboxSubscriptions,  # type: ignore[type-abstract]
        lambda resolver: resolver.require(DurableSubscriptions),
        requires=(DurableSubscriptions,),
    )
    router = APIRouter()
    registry.router(router)

    def bind(resolver: Resolver) -> None:
        router.include_router(
            create_router(
                delivery=resolver.require(EventDelivery),
                notifier=resolver.require(BoundedNotifier),
                settings=resolver.require(HubSettings),  # type: ignore[type-abstract]
            )
        )

    registry.bind("events.router", bind)

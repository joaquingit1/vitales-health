from collections.abc import Callable
from datetime import timedelta
from typing import Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.events.application.delivery import EventStore
from crew_health.events.domain.errors import failure
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork


class SubscriberRegistry(Protocol):
    def subscribers(self) -> tuple[str, ...]: ...


class EventRetention:
    def __init__(
        self,
        *,
        repository: EventStore,
        settings: HubSettings,
        advance: Callable[[UnitOfWork, int], None],
        registry: SubscriberRegistry,
    ) -> None:
        """Retention reads the durable subscriber registry itself, so it waits for exactly the registered
        subscribers; a registered subscriber without a cursor holds retention at the current watermark."""
        self.repository, self.settings, self.advance, self.registry = repository, settings, advance, registry

    def prune(self, uow: UnitOfWork) -> int:
        cutoff = uow.now - timedelta(hours=self.settings.integer("events.retention_hours"))
        watermark = self.repository.watermark(uow.connection)
        scanned = self.repository.scan(
            uow.connection, after=watermark, limit=self.settings.integer("events.page_size")
        )
        through = watermark
        for event in scanned:
            if event.emitted_at > cutoff:
                break
            through = event.seq
        subscribers = self.registry.subscribers()
        if len(subscribers) > self.settings.integer("events.max_subscribers"):
            raise failure(ErrorCode.CONFIG_INVALID)
        for subscriber in subscribers:
            position = self.repository.cursor(uow.connection, subscriber)
            through = min(through, watermark if position is None else position)
        if through <= self.repository.watermark(uow.connection):
            return 0
        self.advance(uow, through)
        return self.repository.delete_through(uow.connection, through)

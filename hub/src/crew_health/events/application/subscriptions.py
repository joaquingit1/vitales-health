from collections.abc import Callable

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.events.application.delivery import EventStore
from crew_health.events.domain.errors import failure
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import Actor, StoredEvent, UnitOfWork, UnitOfWorkFactory


class DurableSubscriptions:
    def __init__(self, *, repository: EventStore, settings: HubSettings, factory: UnitOfWorkFactory) -> None:
        self.repository, self.settings, self.factory = repository, settings, factory
        self.handlers: dict[str, tuple[frozenset[EventType], Callable[[UnitOfWork, StoredEvent], None]]] = {}

    def register(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None:
        if (
            not subscriber
            or len(subscriber.encode()) > 1024
            or not types
            or subscriber in self.handlers
            or len(self.handlers) >= self.settings.integer("events.max_subscribers")
        ):
            raise failure(ErrorCode.CONFIG_INVALID)
        self.handlers[subscriber] = types, handler

    def process(self, subscriber: str) -> int:
        if subscriber not in self.handlers:
            raise failure(ErrorCode.CONFIG_INVALID)
        types, handler = self.handlers[subscriber]
        with self.factory.begin(
            actor=Actor("engine", "engine_events"), correlation_id="correlation_events"
        ) as uow:
            position = self.repository.cursor(uow.connection, subscriber)
            if position < self.repository.watermark(uow.connection):
                raise failure(ErrorCode.CURSOR_EXPIRED)
            scanned = self.repository.scan(
                uow.connection, after=position, limit=self.settings.integer("events.page_size")
            )
            for event in scanned:
                if event.type in types:
                    handler(uow, event)
                self.repository.advance_cursor(uow.connection, subscriber, event.seq)
            return len(scanned)

    def rebuild(
        self, uow: UnitOfWork, subscriber: str, *, rebuild_effects: Callable[[UnitOfWork], None]
    ) -> None:
        if subscriber not in self.handlers:
            raise failure(ErrorCode.CONFIG_INVALID)
        rebuild_effects(uow)
        self.repository.advance_cursor(uow.connection, subscriber, self.repository.head(uow.connection))

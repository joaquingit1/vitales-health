from collections.abc import Callable, Sequence
from datetime import timedelta

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.events.application.delivery import EventStore
from crew_health.events.domain.errors import failure
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork


class EventRetention:
    def __init__(
        self,
        *,
        repository: EventStore,
        settings: HubSettings,
        advance: Callable[[UnitOfWork, int], None],
        subscribers: Sequence[str],
    ) -> None:
        if len(subscribers) > settings.integer("events.max_subscribers") or len(set(subscribers)) != len(
            subscribers
        ):
            raise failure(ErrorCode.CONFIG_INVALID)
        self.repository, self.settings, self.advance, self.subscribers = (
            repository,
            settings,
            advance,
            tuple(subscribers),
        )

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
        for subscriber in self.subscribers:
            through = min(through, self.repository.cursor(uow.connection, subscriber))
        if through <= self.repository.watermark(uow.connection):
            return 0
        self.advance(uow, through)
        return self.repository.delete_through(uow.connection, through)

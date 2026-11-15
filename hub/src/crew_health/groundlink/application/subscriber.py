"""SYSTEM 5.7: isolate poison deliveries without hiding infrastructure failures."""

from collections.abc import Callable

from crew_health.groundlink.application.repository import QuarantineRecord
from crew_health.groundlink.application.service import GroundLinkDeps
from crew_health.groundlink.domain.errors import BundleConflict
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import StoredEvent, UnitOfWork


class DurableHandler:
    def __init__(self, deps: GroundLinkDeps, subscriber: str, settings: HubSettings) -> None:
        self.deps = deps
        self.subscriber = subscriber
        self.settings = settings
        self.attempts: dict[str, tuple[int, int]] = {}

    def deliver(
        self,
        uow: UnitOfWork,
        event: StoredEvent,
        handle: Callable[[UnitOfWork, StoredEvent], None],
    ) -> None:
        # A page contains at most page_size consecutive event sequences. Older failures
        # belong to committed pages; retain this page's counters even after a rollback.
        page_size = self.settings.integer("events.page_size")
        self.attempts = {
            key: value for key, value in self.attempts.items() if value[0] > event.seq - page_size
        }
        try:
            with uow.savepoint("groundlink_delivery"):
                handle(uow, event)
        except StorageUnavailable:
            raise
        except Exception as error:
            attempts = self.attempts.get(event.event_id, (event.seq, 0))[1] + 1
            self.attempts[event.event_id] = (event.seq, attempts)
            if attempts < self.deps.settings.subscriber_max_attempts:
                raise
            reason = (
                error.code.value
                if isinstance(error, ProblemError)
                else "bundle_conflict"
                if isinstance(error, BundleConflict)
                else type(error).__name__
            )
            self.deps.repository.quarantine(
                uow,
                QuarantineRecord(
                    event.event_id,
                    self.subscriber,
                    event.type.value,
                    reason,
                    attempts,
                    uow.now,
                ),
            )

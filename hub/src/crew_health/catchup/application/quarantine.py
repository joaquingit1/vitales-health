"""A bad event never wedges a durable subscriber: bounded redeliveries, then a recorded quarantine.

DurableSubscriptions runs a page in one transaction, so a handler that keeps raising would stop the cursor forever.
Storage errors always propagate (the runner retries them). Any other failure is retried up to the setting, counted
per process, then the handler's own writes are rolled back and the event is quarantined with a safe reason code.
"""

import threading
from collections.abc import Callable
from datetime import datetime

from crew_health.catchup.application.ports import QuarantineRecord
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.uow import StoredEvent, UnitOfWork

_COUNTED_MAX = 1024


def reason_of(error: Exception) -> str:
    if isinstance(error, ProblemError):
        return error.code.value
    return type(error).__name__


class Quarantine:
    def __init__(
        self,
        subscriber: str,
        max_attempts: int,
        record: Callable[[UnitOfWork, QuarantineRecord, str], None],
    ) -> None:
        self._subscriber, self._max, self._record = subscriber, max_attempts, record
        self._lock = threading.Lock()
        self._attempts: dict[str, int] = {}

    def run(
        self, uow: UnitOfWork, event: StoredEvent, handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None:
        try:
            with uow.savepoint("catchup_subscriber"):
                handler(uow, event)
        except StorageUnavailable:
            raise
        except Exception as error:
            attempts = self._count(event.event_id)
            if attempts < self._max:
                raise
            self._forget(event.event_id)
            at: datetime = uow.now
            record = QuarantineRecord(event.event_id, self._subscriber, reason_of(error), attempts, at)
            self._record(uow, record, event.type.value)
            return
        self._forget(event.event_id)

    def _count(self, event_id: str) -> int:
        with self._lock:
            if event_id not in self._attempts and len(self._attempts) >= _COUNTED_MAX:
                del self._attempts[next(iter(self._attempts))]
            self._attempts[event_id] = self._attempts.get(event_id, 0) + 1
            return self._attempts[event_id]

    def _forget(self, event_id: str) -> None:
        with self._lock:
            self._attempts.pop(event_id, None)

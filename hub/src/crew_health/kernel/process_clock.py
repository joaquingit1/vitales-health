from datetime import UTC, datetime
from time import monotonic_ns
from collections.abc import Callable

from crew_health.kernel.ids import require_id


class ProcessClock:
    def __init__(self, process_epoch: str, *, utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic: Callable[[], int] = monotonic_ns) -> None:
        require_id(process_epoch)
        self._epoch = process_epoch
        self._now, self._monotonic = utc_now, monotonic

    def now(self) -> datetime:
        return self._now()

    def monotonic_ns(self) -> int:
        return self._monotonic()

    def process_epoch(self) -> str:
        return self._epoch

"""E18's process clock: the kernel `Clock` port (SYSTEM.md 5.2) read from the operating system.

The hub kernel stays pure, so the only wall and monotonic reads of an E18 command live here, in an adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic_ns

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProcessClock:
    def __init__(
        self,
        process_epoch: str,
        *,
        utc_now: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], int] = monotonic_ns,
    ) -> None:
        self._epoch = require_id(process_epoch)
        self._now, self._monotonic = utc_now, monotonic

    def now(self) -> datetime:
        value = self._now()
        require_utc(value)
        return value

    def monotonic_ns(self) -> int:
        return self._monotonic()

    def process_epoch(self) -> str:
        return self._epoch

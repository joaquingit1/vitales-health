"""The worker's real clock: UTC wall time, a monotonic clock and its segment (one per process start, SYSTEM 5.2)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime


class SystemClock:
    def __init__(self, *, segment: str, poll_ms: int) -> None:
        self._segment, self._poll_s = segment, poll_ms / 1000

    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def segment(self) -> str:
        return self._segment

    def wait_until_ns(self, monotonic_ns: int, should_stop: Callable[[], bool]) -> None:
        while not should_stop():
            left = monotonic_ns - time.monotonic_ns()
            if left <= 0:
                return
            time.sleep(min(left / 1e9, self._poll_s))

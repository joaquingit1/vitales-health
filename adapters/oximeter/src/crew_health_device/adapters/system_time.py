"""Clock reads are isolated at the process boundary."""

import time
from datetime import UTC, datetime


class SystemTimeSource:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

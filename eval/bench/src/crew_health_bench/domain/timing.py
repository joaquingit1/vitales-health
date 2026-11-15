from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.clock import Clock
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.domain.errors import BenchError


def elapsed(start: tuple[str, int], clock: Clock) -> Quantity:
    epoch, monotonic_ns = start
    duration = clock.monotonic_ns() - monotonic_ns
    if epoch != clock.process_epoch() or duration < 0:
        raise BenchError("interrupted")
    return Quantity(Decimal(duration) / Decimal(1000000000), Unit("s"))

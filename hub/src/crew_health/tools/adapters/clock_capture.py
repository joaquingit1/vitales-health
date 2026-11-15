"""Time captures for a turn's elapsed deadline through the shared clock seam (SYSTEM 5.4; E12 review r1 finding 4).

A capture is the hub's current time on the anchor's own source clock and segment, at the latest correction version
ClockCorrections reports. ClockCorrections.elapsed then measures the turn; a capture it cannot place yields no time,
which the gateway treats as expired. No local clock algorithm: history() is read, never interpreted here.
"""

from __future__ import annotations

from collections.abc import Callable

from crew_health.kernel.clock import Clock, ClockCorrections, TimeInput
from crew_health.kernel.uow import UnitOfWork
from crew_health.tools.domain.errors import ToolsInvalid


class CorrectedCapture:
    def __init__(
        self, clock: Clock, corrections: Callable[[], ClockCorrections | None], *, page: int
    ) -> None:
        if not 1 <= page <= 1000:
            raise ToolsInvalid("CONFIG_INVALID")
        self._clock, self._corrections, self._page = clock, corrections, page

    def __call__(self, uow: UnitOfWork, anchor: TimeInput) -> TimeInput | None:
        corrections = self._corrections()
        if corrections is None:
            return None
        version = anchor.source_clock_version
        for _ in range(self._page):
            later = corrections.history(uow, after_version=version, limit=self._page)
            if not later:
                break
            newest = max(item.version for item in later)
            if newest <= version:
                return None
            version = newest
            if len(later) < self._page:
                break
        else:
            return None
        return TimeInput(
            self._clock.now(),
            anchor.source_clock_id,
            anchor.segment_id,
            version,
            anchor.basis,
            anchor.origin_mode,
        )

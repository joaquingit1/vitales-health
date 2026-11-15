"""Routine's HubClock over the shared clock (B2, M3): the composition adapter that lets elapsed timers anchor on a
capture of the hub's own clock in create_app.

E01's assembly builds the same capture privately (app/assembly.py `_hub_time`: the hub source clock's latest
segment and the correction revision in force now) and exposes no port for it, and routine may not import the clock
module's application layer. Clock registers PersistentClocks under the kernel ClockCorrections port, so module.py
checks that provider against the structure below and wraps it. Nothing here computes or corrects time: the capture
is the clock repository's own answer, and every normalization stays PersistentClocks.normalize. When E01 provides a
hub time port, module.py resolves it instead and this file goes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crew_health.kernel.clock import ClockSegment, TimeInput
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.application.ports import HubClock, OriginMode


@runtime_checkable
class ClockSegmentHistory(Protocol):
    def latest_segment(self, connection: StorageSession, source: str) -> ClockSegment | None: ...
    def correction_head(self, connection: StorageSession) -> int: ...


@runtime_checkable
class HubClockSource(Protocol):
    hub_source_clock_id: str | None
    repository: object


class SharedHubClock:
    def __init__(self, source_clock_id: str, history: ClockSegmentHistory) -> None:
        self.source_clock_id, self.history = source_clock_id, history

    def hub_time(self, uow: UnitOfWork, origin_mode: OriginMode) -> TimeInput | None:
        """None while the hub clock has no open segment (before the CLOCK startup phase)."""
        segment = self.history.latest_segment(uow.connection, self.source_clock_id)
        if segment is None:
            return None
        version = self.history.correction_head(uow.connection)
        return TimeInput(
            uow.now, segment.source_clock_id, segment.segment_id, version, "hub_corrected", origin_mode
        )


def shared_hub_clock(corrections: object) -> HubClock | None:
    if not isinstance(corrections, HubClockSource) or corrections.hub_source_clock_id is None:
        return None
    if not isinstance(corrections.repository, ClockSegmentHistory):
        return None
    return SharedHubClock(corrections.hub_source_clock_id, corrections.repository)

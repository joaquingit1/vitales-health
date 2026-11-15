"""The shared mission clock as routine reads it: before an operator sets the mission clock there is no mission day,
which is missing context, not a fault (SYSTEM 5.10).

The clock module raises its MissingMissionClock as a ProblemError with code CONFIG_INVALID (clock/domain/errors.py),
the only CONFIG_INVALID its MissionClock answers; routine may not import the clock's domain to name that class.
This wrapper turns it into routine's MissionClockUnset, which the routine API answers with CONTEXT_INAPPLICABLE and
the startup sweep and day job treat as nothing to do yet. Every other answer is the clock's own."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MetLabel, MissionClock, MissionClockVersion
from crew_health.routine.domain.errors import MissionClockUnset


def _read[T](read: Callable[[], T]) -> T:
    try:
        return read()
    except ProblemError as error:
        if error.code is ErrorCode.CONFIG_INVALID:
            raise MissionClockUnset() from error
        raise


class RoutineMissionClock:
    def __init__(self, clock: MissionClock) -> None:
        self.clock = clock

    def version_at(self, at: datetime) -> MissionClockVersion:
        return _read(lambda: self.clock.version_at(at))

    def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
        return _read(lambda: self.clock.met(at, version=version))

    def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]:
        return _read(lambda: self.clock.label(at, version=version))

    def mission_day(self, at: datetime, *, version: str | None = None) -> int:
        return _read(lambda: self.clock.mission_day(at, version=version))

    def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]:
        return _read(lambda: self.clock.day_bounds(mission_day, version=version))

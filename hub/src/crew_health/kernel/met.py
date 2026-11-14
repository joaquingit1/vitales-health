from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.kernel.clock import require_utc


@dataclass(frozen=True, slots=True)
class MissionClockVersion:
    clock_version: str
    generation: int
    epoch: datetime
    effective_from: datetime
    effective_until: datetime | None
    day_label_template: str

    def __post_init__(self) -> None:
        require_utc(self.epoch)
        require_utc(self.effective_from)
        if self.effective_until is not None:
            require_utc(self.effective_until)


@dataclass(frozen=True, slots=True)
class MetLabel:
    met: str
    mission_day: int
    day_label: str
    clock_version: str


class MissionClock(Protocol):
    def version_at(self, at: datetime) -> MissionClockVersion: ...
    def met(self, at: datetime, *, version: str | None = None) -> MetLabel: ...
    def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]: ...
    def mission_day(self, at: datetime, *, version: str | None = None) -> int: ...
    def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]: ...

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.kernel.clock import require_utc
from crew_health.kernel.config import _FrozenMapping

if TYPE_CHECKING:
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class AbsoluteDeadline:
    at: datetime

    def __post_init__(self) -> None:
        require_utc(self.at)


@dataclass(frozen=True, slots=True)
class ElapsedDeadline:
    anchor: TimeInput
    duration: timedelta


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    key: str
    handler: str
    deadline: AbsoluteDeadline | ElapsedDeadline
    payload: Mapping[str, str]
    ownership_epoch: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _FrozenMapping(self.payload))


class Scheduler(Protocol):
    def replace(self, uow: UnitOfWork, job: ScheduledJob, *, expected_generation: int | None) -> int: ...
    def cancel(self, uow: UnitOfWork, key: str, *, expected_generation: int) -> bool: ...


class SchedulerRegistry(Protocol):
    def register(self, handler: str, fn: Callable[[UnitOfWork, ScheduledJob], None]) -> None: ...


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: str
    code: str | None
    since: datetime | None

    def __post_init__(self) -> None:
        if self.since is not None:
            require_utc(self.since)


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: Literal["ok", "degraded"]  # internal enum
    components: tuple[ComponentHealth, ...]


class HealthState(Protocol):
    def mark_degraded(self, component: str, code: str, since: datetime) -> None: ...
    def mark_ok(self, component: str) -> None: ...
    def snapshot(self) -> HealthSnapshot: ...

"""Ports of the participation module (E22 4.2).

Consumed here: `ParticipationRoster` and `RecordOrigins`, composed over E02's real `IdentityPorts` (the roster and
its mission partition), and E01's `MissionClock`. Provided: `CompletionRecorder`, `ParticipationSettle`,
`StreakQueries`, `PatchQueries` and `CrewGoalQueries`. No port carries a value, only ids, days and flags."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.participation.domain.types import (
    CompletionBasis,
    CrewGoalView,
    PatchAward,
    StreakView,
)

type View = ReadSnapshot | UnitOfWork
type Namespace = Literal["LIVE", "REPLAY", "SIM"]


@dataclass(frozen=True, slots=True)
class RosterMember:
    person_id: str
    from_day: int
    until_day: int | None


class ParticipationRoster(Protocol):
    def partition(self, view: View) -> tuple[str, Namespace] | None: ...
    def members(self, view: View) -> Sequence[RosterMember]: ...
    def first_mission_day(self, view: View, person_id: str) -> int | None: ...


class RecordOrigins(Protocol):
    def record_origin(self, view: View, person_id: str) -> Namespace | None: ...


@dataclass(frozen=True, slots=True)
class IntakeOutcome:
    status: Literal["queued", "duplicate", "conflict", "deferred"]
    intent_key: str | None


class CompletionRecorder(Protocol):
    def record(self, uow: UnitOfWork, basis: CompletionBasis) -> IntakeOutcome: ...


class ParticipationSettle(Protocol):
    def settle(self, intent_key: str) -> Literal["settled", "pending", "parked"]: ...


class StreakQueries(Protocol):
    def for_person(self, person_id: str) -> StreakView: ...


class PatchQueries(Protocol):
    def for_person(self, person_id: str) -> Sequence[PatchAward]: ...


class CrewGoalQueries(Protocol):
    def current(self) -> CrewGoalView: ...

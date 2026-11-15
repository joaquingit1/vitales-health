"""Types of the participation domain (E22 3.1). Every input carries references and flags only: there is no field
for a value, presence, quality, outcome, symptom count or journal fact, and no way to add one."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from crew_health.kernel.ids import ID_PATTERN
from crew_health.participation.domain.errors import InvalidBasis

type Basis = Literal["daily_check", "concern_reported", "episode_updated"]
type ViewOrigin = Literal["LIVE", "SIM"]
type InputOrigin = Literal["LIVE", "REPLAY", "SIM"]
type CauseKind = Literal["answer", "urgent_event"]
type CompletionStatus = Literal["accepted", "rejected"]
type StatusReason = Literal["ORIGIN_MISMATCH", "IDENTITY_MISMATCH", "REQUIRED_ANSWER_MISSING"]
type DayStatus = Literal["checked", "missed", "due"]

BASES: Final[tuple[str, ...]] = ("daily_check", "concern_reported", "episode_updated")
INPUT_ORIGINS: Final[tuple[str, ...]] = ("LIVE", "REPLAY", "SIM")
CAUSE_KINDS: Final[tuple[str, ...]] = ("answer", "urgent_event")
_OFFSET = re.compile(r"^[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_ID = re.compile(ID_PATTERN)


def is_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _require_id(name: str, value: object) -> None:
    if not is_id(value):
        raise InvalidBasis(f"{name} is not a valid id")


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletionBasis:
    """The only input a producer can pass; references only (E22 3.1)."""

    basis: Basis
    basis_ref: str
    patient_id: str
    mission_id: str
    origin_mode: InputOrigin
    occurred_at: datetime
    original_offset: str | None
    actor_person_id: str | None
    cause_kind: CauseKind | None
    cause_ref: str | None
    required_answers_complete: bool | None

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise InvalidBasis("basis is not one of the contract values")
        if self.origin_mode not in INPUT_ORIGINS:
            raise InvalidBasis("origin_mode is not one of the contract values")
        for name in ("basis_ref", "patient_id", "mission_id"):
            _require_id(name, getattr(self, name))
        if self.actor_person_id is not None:
            _require_id("actor_person_id", self.actor_person_id)
        _require_aware_utc(self.occurred_at)
        if self.original_offset is not None and (
            not isinstance(self.original_offset, str) or _OFFSET.fullmatch(self.original_offset) is None
        ):
            raise InvalidBasis("original_offset is malformed")
        self._check_basis_fields()

    def _check_basis_fields(self) -> None:
        episode = self.basis == "episode_updated"
        if episode:
            self._check_episode_fields()
        elif self.cause_kind is not None or self.cause_ref is not None:
            raise InvalidBasis("cause fields belong to episode_updated only")
        if self.basis == "daily_check":
            if not isinstance(self.required_answers_complete, bool):
                raise InvalidBasis("daily_check needs required_answers_complete as a boolean")
        elif self.required_answers_complete is not None:
            raise InvalidBasis("required_answers_complete belongs to daily_check only")

    def _check_episode_fields(self) -> None:
        if self.cause_kind not in CAUSE_KINDS:
            raise InvalidBasis("episode_updated needs a cause_kind")
        _require_id("cause_ref", self.cause_ref)


def _require_aware_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise InvalidBasis("occurred_at must be an aware UTC instant")


@dataclass(frozen=True, slots=True)
class CompletionFact:
    """What the derivation sees of an accepted, non voided completion."""

    completion_id: str
    patient_id: str
    mission_day: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletionRecord:
    """A stored CheckCompletion (CONTRACTS 8) with its lineage (E22 3.5)."""

    completion_id: str
    intent_key: str
    lineage_id: str
    generation: int
    replaces_completion_id: str | None
    basis: CompletionBasis
    status: CompletionStatus
    status_reason: StatusReason | None
    mission_clock_version: str
    mission_day: int
    completion_rule_version: str

    def __post_init__(self) -> None:
        if (self.status == "rejected") != (self.status_reason is not None):
            raise InvalidBasis("a rejected completion has a reason and an accepted one has none")

    def fact(self) -> CompletionFact:
        return CompletionFact(
            self.completion_id, self.basis.patient_id, self.mission_day, self.basis.occurred_at
        )


@dataclass(frozen=True, slots=True)
class DayEntry:
    mission_day: int
    status: DayStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class StreakView:
    patient_id: str
    mission_id: str
    origin_mode: ViewOrigin
    as_of: datetime
    current_mission_day: int
    today: Literal["checked", "due"]
    current_streak_days: int
    longest_streak_days: int
    completed_days_total: int
    first_mission_day: int
    days: tuple[DayEntry, ...]
    derivation_version: str
    mission_clock_version: str
    event_cursor: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PatchAward:
    award_id: str
    patient_id: str
    mission_id: str
    origin_mode: ViewOrigin
    threshold: int
    patch_design_id: str
    reached_by_completion_id: str
    reached_on_mission_day: int
    derivation_version: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CrewGoalView:
    """The only crew visible participation object. No person, no per person count, no per day count."""

    mission_id: str
    origin_mode: ViewOrigin
    window_first_mission_day: int
    window_last_mission_day: int
    completed_total: int
    due_total: int
    reached: bool
    as_of: datetime
    derivation_version: str
    mission_clock_version: str

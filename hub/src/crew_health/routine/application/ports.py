from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

from crew_health.kernel.clock import NormalizedTime, TimeInput
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.routine.application.store_port import RoutineStore as RoutineStore
from crew_health.routine.domain.release import RoutineRelease
from crew_health.routine.domain.vocabulary import OccurrenceState, OriginMode, SkipReason


# Provided by E23 (crew_health.mission); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class MissionFacts:
    mission_id: str
    mission_mode: OriginMode
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class MissionStatePort(Protocol):
    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class LocationPort(Protocol):
    def location_of(self, person_id: str, at: datetime) -> str | None: ...


# Provided by E02 (crew_health.identity); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class RosterMember:
    person_id: str
    display_name: str
    roster_index: int
    medical_officer_rank: int | None


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RosterPort(Protocol):
    def crew(self, at: datetime) -> Sequence[RosterMember]: ...
    def members(self) -> Sequence[RosterMember]: ...
    def person_ids(self) -> frozenset[str]: ...


# Provided by E01 (crew_health.records); structural copy until it is on main.
class AnswerFacts(Protocol):
    @property
    def answer_id(self) -> str: ...
    @property
    def question_id(self) -> str: ...
    @property
    def presence(self) -> str: ...
    @property
    def answered_at(self) -> datetime: ...
    @property
    def origin_mode(self) -> OriginMode: ...
    @property
    def patient_id(self) -> str: ...
    @property
    def mission_id(self) -> str: ...


# The fields of records' ObservationRecord (crew_health.records.domain.observations) that routine reads.
class ObservationFacts(Protocol):
    @property
    def observation_id(self) -> str: ...
    @property
    def patient_id(self) -> str: ...
    @property
    def mission_id(self) -> str: ...
    @property
    def code(self) -> str: ...
    @property
    def quantity(self) -> Quantity: ...
    @property
    def observed(self) -> TimeInput: ...
    @property
    def normalized(self) -> NormalizedTime: ...
    @property
    def origin_mode(self) -> OriginMode: ...
    @property
    def quality_status(self) -> str: ...


# Provided by E01 (crew_health.records): the read half of records.application.ports.RecordsStore, which the records
# repository already satisfies. A structural copy because records' ports module imports records.domain, which the
# generated contract routine-independent-records forbids routine to reach; composition registers the records store
# under this class as well (E01 app). Absent: no record-driven bindings and linked records are refused.
class RecordFacts(Protocol):
    def observation(
        self, view: ReadSnapshot | UnitOfWork, observation_id: str
    ) -> ObservationFacts | None: ...
    def answer(self, view: ReadSnapshot | UnitOfWork, answer_id: str) -> AnswerFacts | None: ...


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RequestAuthenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext: ...


type OccurrenceView = dict[str, object]
type OverrideView = dict[str, object]


@dataclass(frozen=True, slots=True)
class OccurrenceActionBody:
    action: Literal["complete", "skip", "later"]
    observation_ids: tuple[str, ...] = ()
    answer_ids: tuple[str, ...] = ()
    skip_reason: SkipReason | None = None
    note: str | None = None
    later_until: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OverrideUntil:
    kind: Literal["days", "person_stops"]
    days: int | None = None


@dataclass(frozen=True, slots=True)
class OverrideCreateBody:
    item_id: str
    trigger_index: int
    person_id: str | None
    until: OverrideUntil
    note: str | None = None


@dataclass(frozen=True, slots=True)
class OccurrenceRef:
    occurrence_id: str
    item_id: str
    state: OccurrenceState
    due_from: datetime | None
    due_until: datetime | None


@dataclass(frozen=True, slots=True)
class TodayRoutinePart:
    due: int
    later: int
    done: int
    open_today: int
    next_occurrence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AddedQuestion:
    question_id: str
    cause: Literal["overlay", "miss", "warning"]
    repeat_after_minutes: int | None


@dataclass(frozen=True, slots=True)
class DrillOccurrence:
    occurrence_id: str
    person_id: str
    item_id: str
    skill_ids: tuple[str, ...]
    state: Literal["due", "later", "done", "missed", "skipped"]


class RoutineQueries(Protocol):
    def active_release_id(self) -> str: ...
    def schedule(self, session: SessionContext, range_: str) -> dict[str, object]: ...
    def occurrences(
        self, session: SessionContext, range_: str, state: str | None, cursor: str | None, limit: int
    ) -> dict[str, object]: ...
    def occurrence(self, session: SessionContext, occurrence_id: str) -> OccurrenceView: ...
    def tasks(self, session: SessionContext, cursor: str | None, limit: int) -> dict[str, object]: ...
    def task(self, session: SessionContext, item_id: str) -> dict[str, object]: ...
    def overrides(self, session: SessionContext, cursor: str | None, limit: int) -> dict[str, object]: ...


class RoutineCommands(Protocol):
    def act(
        self,
        session: SessionContext,
        occurrence_id: str,
        body: OccurrenceActionBody,
        *,
        if_match: int | None,
        idempotency_key: str,
        config_release: str | None,
    ) -> tuple[int, OccurrenceView]: ...
    def create_override(
        self,
        session: SessionContext,
        body: OverrideCreateBody,
        *,
        idempotency_key: str,
        config_release: str | None,
    ) -> tuple[int, OverrideView]: ...
    def end_override(
        self,
        session: SessionContext,
        override_id: str,
        *,
        if_match: int | None,
        idempotency_key: str,
        config_release: str | None = None,
        note: str | None = None,
    ) -> tuple[int, OverrideView]: ...


class RoutineTodayPort(Protocol):
    def due_today(self, person_id: str, at: datetime) -> Sequence[OccurrenceRef]: ...
    def today_summary(self, session: SessionContext, at: datetime) -> TodayRoutinePart: ...


class DailyCheckQuestionSource(Protocol):
    def added_questions(self, person_id: str, mission_day: int, at: datetime) -> Sequence[AddedQuestion]: ...


class RoutineCompletionPort(Protocol):
    def drill_occurrence(self, occurrence_id: str) -> DrillOccurrence | None: ...
    def complete(self, occurrence_id: str, completed_at: datetime, actor: Actor) -> bool: ...


class AssistantRoutinePort(Protocol):
    def add_recheck(
        self, uow: UnitOfWork, *, person_id: str, item_id: str, insight_id: str, due_from: datetime
    ) -> OccurrenceRef: ...


class ReleaseSource(Protocol):
    def release(self, handle: ReleaseHandle) -> RoutineRelease: ...
    def cached(self, release_id: str) -> RoutineRelease | None: ...
    def pinned(self, release_id: str) -> RoutineRelease | None: ...


# Provided by E02 (crew_health.identity) per SYSTEM 8.3; structural copy until it is on main.
class Visibility(Protocol):
    def can_read(self, session: SessionContext, object_ref: str) -> bool: ...


# Provided by E24 (crew_health.warnings); structural copy until it is on main.
class WarningCommands(Protocol):
    def attach_routine_overrides(
        self, uow: UnitOfWork, warning_id: str, override_ids: Sequence[str]
    ) -> None: ...


# Provided by E03 (crew_health.dailycheck); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class DailyCheckSubmitted:
    event_type: ClassVar[str] = "dailycheck.submitted"
    daily_check_id: str
    person_id: str
    mission_day: int
    origin_mode: OriginMode
    submitted_at: datetime


# Provided by E03 (crew_health.dailycheck); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class DailyCheckAbandoned:
    event_type: ClassVar[str] = "dailycheck.abandoned"
    daily_check_id: str
    person_id: str
    mission_day: int


# Provided by E03 (crew_health.dailycheck); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class ReactionTestRecorded:
    event_type: ClassVar[str] = "reaction_test.recorded"
    reaction_test_id: str
    person_id: str
    state: str
    occurrence_id: str | None

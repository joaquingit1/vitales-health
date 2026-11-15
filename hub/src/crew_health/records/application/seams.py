"""Records-internal seams for explicit assembly injection (they carry records.domain types, so no other module
imports this file; the cross-module ports live in records/application/ports.py)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.records.domain.answers import AnswerRecord
from crew_health.records.domain.observations import (
    AcquisitionMethod,
    ObservationInput,
    ObservationPolicy,
    ObservationRecord,
    OriginMode,
    RecordContext,
)


class RecordAuthorization(Protocol):
    def require_read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        object_ref: str,
        patient_id: str,
        fields: frozenset[str],
    ) -> None: ...
    def require_write(self, uow: UnitOfWork, session: SessionContext, context: RecordContext) -> None: ...


class SubjectBinding(Protocol):
    def bind(self, uow: UnitOfWork, session: SessionContext, value: ObservationInput) -> RecordContext: ...


class ObservationPolicies(Protocol):
    def for_code(self, release: ReleaseHandle, code: str) -> ObservationPolicy: ...


class ProducerProof(Protocol):
    def verify(
        self, uow: UnitOfWork, session: SessionContext, value: ObservationInput, context: RecordContext
    ) -> str: ...


class EpisodeBinding(Protocol):
    def require(
        self, uow: UnitOfWork, session: SessionContext, *, episode_id: str, context: RecordContext
    ) -> None: ...


class AnswerWorkflow(Protocol):
    def require(self, uow: UnitOfWork, session: SessionContext, answer: AnswerRecord) -> None: ...


class RecordsStore(Protocol):
    def observation(
        self, view: ReadSnapshot | UnitOfWork, observation_id: str
    ) -> ObservationRecord | None: ...
    def answer(self, view: ReadSnapshot | UnitOfWork, answer_id: str) -> AnswerRecord | None: ...
    def add_observation(self, uow: UnitOfWork, value: ObservationRecord) -> None: ...
    def add_answer(self, uow: UnitOfWork, value: AnswerRecord) -> None: ...
    def observations(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        mission_id: str,
        origin_mode: OriginMode,
        code: str,
        limit: int,
        accepted_only: bool = True,
        before: datetime | None = None,
        source: tuple[AcquisitionMethod, str | None] | None = None,
        states: tuple[str, ...] | None = None,
        corrected_only: bool = False,
    ) -> tuple[ObservationRecord, ...]: ...
    def has_more_observations(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        mission_id: str,
        origin_mode: OriginMode,
        code: str,
        after: int,
        accepted_only: bool = True,
        states: tuple[str, ...] | None = None,
    ) -> bool: ...
    def answers(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        mission_id: str,
        origin_mode: OriginMode,
        question_id: str,
        limit: int,
    ) -> tuple[AnswerRecord, ...]: ...


class ObservationEvaluation(Protocol):
    def observation_accepted(self, uow: UnitOfWork, observation: ObservationRecord) -> None: ...


class MissionFacts(Protocol):
    @property
    def mission_id(self) -> str: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main. Every observation a producer appends is
# partitioned by the current mission, which only E23 knows: without it the append fails closed.
class MissionStatePort(Protocol):
    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None: ...

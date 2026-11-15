"""Local seams for explicit assembly injection. R-E01-17 consumer bindings remain unexported."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.records.domain.answers import AnswerRecord
from crew_health.records.domain.observations import (
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
    ) -> tuple[ObservationRecord, ...]: ...
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

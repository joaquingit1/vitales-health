"""E15 section 4.2 canonical ports and consumer-owned optional seams.

All public DTOs remain independent of domain/adapters so consumers can import these exact class objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from crew_health.episodes.application.dto import (
    Answer,
    CrewMember,
    EpisodeFact,
    EpisodeRef,
    EpisodeToolFacts,
    NewEpisode,
    ObservationFact,
    Viewer,
)
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork

type View = ReadSnapshot | UnitOfWork
type EpisodeView = dict[str, object]


class EpisodeKernel(Protocol):
    def create(self, uow: UnitOfWork, draft: NewEpisode) -> EpisodeRef: ...
    def facts(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None: ...
    def record_route(
        self, uow: UnitOfWork, episode_id: str, module_id: str, area_answer_id: str, at: datetime
    ) -> None: ...
    def view(self, uow: UnitOfWork, viewer: Viewer, episode_id: str) -> EpisodeView: ...


class EpisodeFacts(Protocol):
    def get(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None: ...
    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool: ...
    def episode_of(self, uow: UnitOfWork, kind: str, ref_id: str) -> str | None: ...


class EpisodeFactsPort(Protocol):
    def facts(self, uow: UnitOfWork, episode_id: str, sections: frozenset[str]) -> EpisodeToolFacts: ...
    def subject_of(self, uow: UnitOfWork, episode_id: str) -> str | None: ...


class RefCheckPort(Protocol):
    def visible(self, ref_id: str, actor: Actor) -> bool: ...


class EpisodeReader(Protocol):
    def subject_of(self, episode_id: str) -> str | None: ...
    def visible(self, session: SessionContext, episode_id: str) -> EpisodeRef | None: ...


class EpisodeRecordsSource(Protocol):
    def items(self, uow: UnitOfWork, person_id: str, cursor: str | None, limit: int) -> dict[str, object]: ...


class QuestionSensitivity(Protocol):
    def is_restricted(self, questionnaire_version: str, question_id: str) -> bool: ...


class IntakeGaps(Protocol):
    def not_answered(self, uow: UnitOfWork, episode_id: str) -> tuple[str, ...]: ...


class AnswerStore(Protocol):
    def for_episode(self, uow: View, episode_id: str) -> Sequence[Answer]: ...
    def question_ids_answered(self, uow: UnitOfWork, episode_id: str) -> frozenset[str]: ...
    def heads(self, uow: UnitOfWork, episode_id: str) -> Mapping[str, Answer]: ...


class ObservationFacts(Protocol):
    def get(self, uow: UnitOfWork, observation_id: str) -> ObservationFact | None: ...


class UrgencyQueries(Protocol):
    def summary(self, uow: View, episode_id: str) -> object: ...
    def blocks_resolution(self, uow: UnitOfWork, episode_id: str) -> bool: ...


class AssessmentFacts(Protocol):
    def citing(self, uow: View, episode_id: str, record_ids: Sequence[str]) -> Sequence[str]: ...
    def ids_for_episode(self, uow: View, episode_id: str) -> Sequence[str]: ...


class AssessmentCommands(Protocol):
    def mark_stale(self, uow: UnitOfWork, assessment_id: str, cause_ref: str) -> None: ...
    def mark_reviewed(self, uow: UnitOfWork, assessment_id: str, review_id: str) -> None: ...


class RosterPort(Protocol):
    def crew(self, at: datetime) -> Sequence[CrewMember]: ...


class ConsentQueries(Protocol):
    def allows(self, uow: View, subject_id: str, recipient_id: str, scope: tuple[str, str]) -> bool: ...


class OriginNamespace(Protocol):
    def origin(self, uow: View, patient_id: str, mission_id: str) -> str | None: ...


class MissionClock(Protocol):
    def day_end(self, instant: datetime) -> datetime: ...


class DayLabelPort(Protocol):
    def inline_label_at(self, instant: datetime) -> str: ...


class SessionAuthenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext | None: ...


class CommandFingerprint(Protocol):
    def fingerprint(self, canonical_body: bytes) -> str: ...


class GapFacts(Protocol):
    def for_episode(self, uow: View, episode_id: str) -> Sequence[Mapping[str, object]]: ...

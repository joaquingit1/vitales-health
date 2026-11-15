"""Assistant public seams and unresolved consumer-owned provider contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

from crew_health.assistant.application.content import CopyDocument
from crew_health.kernel.task_results import TaskFailureResults as TaskFailureResults
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork


class SessionAuthenticator(Protocol):
    """E02: exact consumer shape already used by measurement and episodes."""

    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext | None: ...


class CommandFingerprint(Protocol):
    """E01 keyed command-body fingerprint. No unkeyed private text digest."""

    def fingerprint(self, canonical_body: bytes) -> str: ...


class AssistantStatusPort(Protocol):
    def state(self) -> Literal["running", "stopped", "loading", "unavailable"]: ...


class InsightFactsPort(Protocol):
    def person_of(self, insight_id: str) -> str | None: ...


class InsightQueries(Protocol):
    def current(self, session: SessionContext) -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class AssessmentChanged:
    event_type: ClassVar[str] = "AssessmentChanged"
    assessment_id: str
    episode_id: str
    validation_status: str
    origin: str
    at: datetime


class IntakeQueries(Protocol):
    """E04, E13 section4.3, pending owner binding."""

    def allowed_question_ids(self, uow: UnitOfWork, episode_id: str) -> frozenset[str]: ...
    def next_question_ids(self, uow: UnitOfWork, episode_id: str) -> tuple[str, ...]: ...


class AnswerRef(Protocol):
    @property
    def answer_id(self) -> str: ...


class EpisodeAnswerPort(Protocol):
    def answer(
        self,
        session: SessionContext,
        episode_id: str,
        question_id: str,
        choice_id: str,
        *,
        idempotency_key: str,
    ) -> AnswerRef: ...


class StepText(Protocol):
    @property
    def step_id(self) -> str: ...
    @property
    def text(self) -> str: ...


class PlanStepTextPort(Protocol):
    """E28, E13 section4.3, pending owner binding."""

    def active_step(self, uow: UnitOfWork, episode_id: str, step_id: str) -> StepText | None: ...
    def first_step(self, uow: UnitOfWork, episode_id: str) -> StepText | None: ...


class RecordAuthorization(Protocol):
    """Exact structural read seam from records.application.seams (domain-bearing owner)."""

    def require_read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        object_ref: str,
        patient_id: str,
        fields: frozenset[str],
    ) -> None: ...


class MissionFacts(Protocol):
    @property
    def mission_id(self) -> str: ...
    @property
    def mission_mode(self) -> Literal["LIVE", "REPLAY", "SIM"]: ...
    @property
    def active_environment_id(self) -> str: ...


class MissionStatePort(Protocol):
    """E23 exact consumer shape already used by help."""

    def current(self, uow: ReadSnapshot | None = None) -> MissionFacts | None: ...


class ReferenceOwner(Protocol):
    """E12 observation/answer support lookup requested by E13 section3.7 VR1/VR4.

    Missing owner resolution cannot admit a personal support reference.
    """

    def owner_of(self, uow: ReadSnapshot | UnitOfWork, kind: str, ref_id: str) -> str | None: ...


class InsightWarningPort(Protocol):
    """E24, E13 section4.3. Owner must join caller's atomic write transaction."""

    def validate_and_raise(
        self, uow: UnitOfWork, draft: Mapping[str, object]
    ) -> Mapping[str, object] | None: ...
    def on_insight_dismissed(self, uow: UnitOfWork, insight_id: str, actor: object) -> None: ...
    def on_insight_expired(self, uow: UnitOfWork, insight_id: str) -> None: ...


class InsightQuery(Protocol):
    def active(self, uow: UnitOfWork) -> Sequence[Mapping[str, object]]: ...


class SessionReader(Protocol):
    """E02 identity.application.ports exact public seam, not yet on this base."""

    def is_live(self, view: ReadSnapshot | UnitOfWork, session: SessionContext, *, listener: str) -> bool: ...
    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool: ...
    def access_generation(self, view: ReadSnapshot | UnitOfWork) -> int: ...


class AssistantCopySource(Protocol):
    """Immutable configuration copy supplied under an explicit release and verified SHA256."""

    def for_release(self, config_release_id: str) -> CopyDocument: ...


@dataclass(frozen=True, slots=True)
class UrgencyToken:
    episode_id: str
    cycle: int
    revision: int


@dataclass(frozen=True, slots=True)
class TurnContext:
    kind: str
    person_id: str | None
    episode_id: str | None


@dataclass(frozen=True, slots=True)
class Proceed:
    token: UrgencyToken | None
    urgent_review_pending: bool
    review_intent_pending: bool = False


@dataclass(frozen=True, slots=True)
class Interrupted:
    help_session_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RedFlagsPending:
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Refused:
    reason: str


type GateDecision = Proceed | Interrupted | RedFlagsPending | Refused


class InferenceGate(Protocol):
    """E11 exact frozen pure decision seam; host adapter needed until its provider lands."""

    def before_turn(self, uow: UnitOfWork, ctx: TurnContext) -> GateDecision: ...
    def still_valid(self, uow: UnitOfWork, token: UrgencyToken) -> bool: ...


class AssistantTurnControl(Protocol):
    def cancel_episode_turns(self, episode_id: str, reason: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DeliveredAssessment:
    handoff_id: str
    delivery_id: str
    assessment_id: str
    episode_id: str
    subject_person_id: str
    recipient_id: str
    selected_fields: frozenset[str]
    content_sha256: str
    body_json: bytes
    config_release_id: str
    evidence_release_id: str
    model_release_id: str | None
    source_revision: int
    delivered_at: datetime


class DeliveredAssessmentSource(Protocol):
    """E16 host bridge: exact approved delivered bytes, after a fresh recipient field-grant check.

    Never enrich this source projection from current E13 or E15 rows.
    """

    def read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        assessment_id: str,
        fields: frozenset[str],
    ) -> DeliveredAssessment | None: ...


@dataclass(frozen=True, slots=True)
class SelectedAssessment:
    assessment_id: str
    episode_id: str
    subject_person_id: str
    source_revision: int
    validation_status: Literal["draft", "stale", "reviewed"]
    config_release_id: str
    evidence_release_id: str
    model_release_id: str | None
    selected_fields: frozenset[str]
    content_sha256: str
    body_json: bytes


class SelectedAssessmentFacts(Protocol):
    """E16 consumes the selected current owner fields for an explicit human sharing preview.

    source_revision is the assessment record's own mutation revision, not an episode revision.
    body_json contains exactly selected_fields. Identity and lineage stay outside the selected body.
    """

    def read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        assessment_id: str,
        fields: frozenset[str],
    ) -> SelectedAssessment: ...

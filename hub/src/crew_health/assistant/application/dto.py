from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.assistant.domain.runtime import Runtime
from crew_health.assistant.domain.turn import TurnState
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.uow import Actor, SessionContext

type ContextKind = Literal["general", "episode", "help_session", "warning", "reading", "insight"]
type WorkKind = Literal["conversation", "assessment", "help", "insight"]


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str
    person_id: str
    context_kind: ContextKind
    context_ref_id: str | None
    config_release_id: str
    model_release_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    created_at: datetime
    state: Literal["open", "closed"] = "open"
    revision: int = 1


@dataclass(frozen=True, slots=True)
class Work:
    turn_id: str
    task_id: str
    kind: WorkKind
    conversation_id: str | None
    episode_id: str | None
    help_session_id: str | None
    help_turn_id: str | None
    subject_person_id: str | None
    actor: Actor
    session: SessionContext | None
    phase: str
    priority: int
    config_release_id: str
    evidence_release_id: str
    model_release_id: str
    source_revision: int
    created_at: datetime
    state: TurnState = TurnState.QUEUED
    preemptions: int = 0
    steps_used: int = 0
    error_code: ErrorCode | None = None
    continuation_key: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeRecord:
    runtime: Runtime
    model_release_id: str | None
    mode: Literal["stub", "live"]
    since: datetime
    error_code: ErrorCode | None = None


@dataclass(frozen=True, slots=True)
class Assessment:
    assessment_id: str
    episode_id: str
    person_id: str
    body: bytes
    completeness: Literal["complete", "incomplete"]
    stop_reason: str | None
    config_release_id: str
    turn_id: str
    job_id: str
    created_at: datetime
    validation_status: Literal["draft", "stale", "reviewed"] = "draft"
    cause_ref: str | None = None
    review_id: str | None = None

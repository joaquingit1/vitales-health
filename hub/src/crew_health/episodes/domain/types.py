from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from crew_health.episodes.domain.onset import Onset
from crew_health.kernel.uow import Actor


class EpisodeState(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    AWAITING_INFORMATION = "awaiting_information"
    AWAITING_REVIEW = "awaiting_review"
    FOLLOWUP = "followup"
    RESOLVED = "resolved"


class ResolutionReason(StrEnum):
    MEASUREMENT_ANOMALY_NOT_REPRODUCED = "measurement_anomaly_not_reproduced"
    SYMPTOMS_REPORTED_RESOLVED = "symptoms_reported_resolved"
    CLINICIAN_ASSESSED = "clinician_assessed"


ACTIVE = frozenset(
    {
        EpisodeState.OPEN,
        EpisodeState.AWAITING_INFORMATION,
        EpisodeState.AWAITING_REVIEW,
        EpisodeState.FOLLOWUP,
    }
)
MR_PENDING = frozenset({"proposed", "awaiting_acceptance", "accepted", "in_progress"})
PLAN_RUN_OPEN = frozenset({"active", "watching", "escalated"})
REOPEN_REASONS = frozenset({"symptoms_recurred", "evidence_changed", "recorded_in_error"})
LINK_KINDS = frozenset(
    {
        "measurement_request",
        "capability_gap",
        "assessment",
        "help_session",
        "plan_run",
        "guided_check",
        "handoff",
        "observation",
    }
)


@dataclass(frozen=True, slots=True)
class TaskFact:
    kind: str
    state: str


@dataclass(frozen=True, slots=True)
class LinkFact:
    kind: str
    state: str | None


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    patient_id: str
    mission_id: str
    origin_mode: str
    concern_text: str
    onset: Onset
    state: EpisodeState
    revision: int
    created_at: datetime
    created_by: Actor
    questionnaire_version: str
    red_flag_ids: tuple[str, ...]
    route_module_id: str | None
    config_release_id: str
    last_resolution_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    observation_id: str
    patient_id: str
    episode_id: str | None
    quality_status: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ResolutionFacts:
    blocks_resolution: bool
    open_tasks: bool
    open_plan_runs: bool
    pending_requests: bool
    present_answer: bool
    observations: tuple[EvidenceObservation, ...]
    review_id: str | None
    review_by: str | None
    review_role: str | None
    review_at: datetime | None
    latest_correction_at: datetime | None


@dataclass(frozen=True, slots=True)
class ResolutionCommand:
    reason: ResolutionReason
    rationale: str | None
    observation_ids: tuple[str, ...]
    review_id: str | None
    actor_id: str
    authority: str

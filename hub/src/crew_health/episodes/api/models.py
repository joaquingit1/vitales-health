"""E15-owned request extensions from design 4.1; schemas generated from these strict boundary models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from crew_health.contracts_gen.schemas.requests_schema import ReviewCreate


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Acknowledge(StrictBody):
    action: Literal["acknowledge"]


class RequestReview(StrictBody):
    action: Literal["request_review"]
    note: str | None = Field(default=None, max_length=500)


class AddFollowup(StrictBody):
    action: Literal["add_followup"]
    action_text: str = Field(min_length=1, max_length=500)
    owner_person_id: str | None = Field(default=None, pattern="^[A-Za-z0-9_]+$")
    due_at: datetime | None = None


class CompleteFollowup(StrictBody):
    action: Literal["complete_followup"]
    task_id: str = Field(pattern="^[A-Za-z0-9_]+$")
    task_revision: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)
    evidence_ref: str | None = Field(default=None, pattern="^[A-Za-z0-9_]+$")


class CancelFollowup(StrictBody):
    action: Literal["cancel_followup"]
    task_id: str = Field(pattern="^[A-Za-z0-9_]+$")
    task_revision: int = Field(ge=1)
    cancellation_reason: str = Field(min_length=1, max_length=500)


class ReassignFollowup(StrictBody):
    action: Literal["reassign_followup"]
    task_id: str = Field(pattern="^[A-Za-z0-9_]+$")
    task_revision: int = Field(ge=1)
    owner_person_id: str = Field(pattern="^[A-Za-z0-9_]+$")


class Resolve(StrictBody):
    action: Literal["resolve"]
    reason: Literal["measurement_anomaly_not_reproduced", "symptoms_reported_resolved", "clinician_assessed"]
    rationale: str | None = Field(default=None, max_length=500)
    observation_ids: list[str] = Field(default_factory=list, max_length=1000)
    review_id: str | None = Field(default=None, pattern="^[A-Za-z0-9_]+$")


class Reopen(StrictBody):
    action: Literal["reopen"]
    reason: Literal["symptoms_recurred", "evidence_changed", "recorded_in_error"]
    note: str | None = Field(default=None, max_length=500)


class OnsetBody(StrictBody):
    at: datetime | None
    precision: Literal["exact", "hour", "day", "unknown"]


class CorrectOnset(StrictBody):
    action: Literal["correct_onset"]
    onset: OnsetBody


type EpisodeAction = Annotated[
    Acknowledge
    | RequestReview
    | AddFollowup
    | CompleteFollowup
    | CancelFollowup
    | ReassignFollowup
    | Resolve
    | Reopen
    | CorrectOnset,
    Field(discriminator="action"),
]


class ReviewRequest(ReviewCreate):
    supersedes_review_id: str | None = Field(default=None, pattern="^[A-Za-z0-9_]+$")


def action_adapter() -> TypeAdapter[EpisodeAction]:
    return TypeAdapter(EpisodeAction)

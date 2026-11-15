# teach.application.ports imports teach's domain (under TYPE_CHECKING), so importing it breaks
# plans-independent-teach. teach/module.py composes these copies with teach's own classes (AGENTS 1 rule 9).
"""Exact structural copies of teach's plan and guided-check seam, resolved through teach's composition adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]


@dataclass(frozen=True, slots=True)
class GuidedCheckCompleted:
    event_type: ClassVar[str] = "teach.guided_check_completed"
    guided_check_id: str
    plan_run_id: str | None
    step_id: str | None
    outcome: Literal["agree", "disagree", "no_reference"]
    observation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GuidedCheckFacts:
    guided_check_id: str
    skill_id: str
    plan_run_id: str | None
    subject_person_id: str
    helper_person_id: str
    measured_record_id: str
    state: str
    result: str | None
    origin_mode: OriginMode
    observation_ids: tuple[str, ...]


class GuidedCheckQueries(Protocol):
    def get(self, view: ReadSnapshot | UnitOfWork, guided_check_id: str) -> GuidedCheckFacts | None: ...


@dataclass(frozen=True, slots=True)
class GuidedStepRef:
    plan_run_id: str
    subject_person_id: str
    episode_id: str | None
    step_id: str
    environment_id: str
    skills_catalog_version: str
    ui_copy_version: str
    run_open: bool
    step_runnable: bool


class PlanRunReader(Protocol):
    def guided_step(self, plan_run_id: str, skill_id: str) -> GuidedStepRef | None: ...


class GuidedCompletionSink(Protocol):
    """Plans' handler for teach's GuidedCheckCompleted, called by teach's adapter inside teach's finish
    transaction (SYSTEM 8.1)."""

    def on_guided_check_completed(self, uow: UnitOfWork, event: GuidedCheckCompleted) -> None: ...

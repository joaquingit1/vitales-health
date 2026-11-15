"""Every executable plan step passes the canonical guidance decision."""

from __future__ import annotations

import logging
from typing import Literal

from crew_health.guidance.application.ports import (
    ContentRef,
    GuidanceDecision,
    GuidanceEligibility,
    GuidanceRequest,
    Requirements,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.plans.domain.availability import EnvContext, assess_step, runnable_now
from crew_health.plans.domain.model import Plan, Step


class StepGate:
    def __init__(self, guidance: GuidanceEligibility | None) -> None:
        self.guidance = guidance

    def decision(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        plan: Plan,
        step: Step,
        origin_mode: Literal["LIVE", "REPLAY", "SIM"],
        subject_person_id: str | None,
        release_id: str,
    ) -> GuidanceDecision | None:
        if self.guidance is None:
            return None
        request = GuidanceRequest(
            content=ContentRef(
                kind="prepare_step" if step.stage_id == "return" else "plan_step",
                id=step.step_id,
                content_sha256=step.content_sha256,
            ),
            purpose="patient_instruction",
            release_id=release_id,
            origin_mode=origin_mode,
            subject_person_id=subject_person_id,
            patient_location_id=None,
            requirements=Requirements(
                instruments=frozenset(step.needs.instruments),
                supplies={need.supply_id: need.per_use for need in step.needs.supplies},
                skills=frozenset(step.needs.skills),
                operator=step.who.kind,
                operator_skill_id=step.who.skill_id,
            ),
            fixed_first_screen=False,
            proposed_operator_id=None,
        )
        try:
            return self.guidance.evaluate(view, request)
        except ProblemError:
            raise
        except Exception:
            logging.getLogger(__name__).warning(
                "plans_guidance_unavailable", extra={"plan_id": plan.plan_id, "step_id": step.step_id}
            )
            return None

    def executable(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        plan: Plan,
        step: Step,
        origin_mode: Literal["LIVE", "REPLAY", "SIM"],
        subject_person_id: str | None,
        release_id: str,
    ) -> bool:
        decision = self.decision(
            view,
            plan=plan,
            step=step,
            origin_mode=origin_mode,
            subject_person_id=subject_person_id,
            release_id=release_id,
        )
        # show_with_substitute carries the substitute's executable bit; it never authorizes the principal.
        return decision is not None and decision.outcome == "show" and decision.executable

    def runnable(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        plan: Plan,
        step: Step,
        origin_mode: Literal["LIVE", "REPLAY", "SIM"],
        subject_person_id: str | None,
        context: EnvContext,
        release_id: str,
    ) -> bool:
        return runnable_now(assess_step(step, plan, context)) and self.executable(
            view,
            plan=plan,
            step=step,
            origin_mode=origin_mode,
            subject_person_id=subject_person_id,
            release_id=release_id,
        )

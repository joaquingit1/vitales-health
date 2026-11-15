"""One step's wire projection with independent domain and guidance eligibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from crew_health.guidance.application.ports import GuidanceDecision
from crew_health.kernel.config import ConfigName
from crew_health.plans.application.eligibility import StepGate
from crew_health.plans.application.ground_log import text
from crew_health.plans.application.release import UiCopy, array_value, object_value, read_document
from crew_health.plans.application.world import PlansWorld, View, due_text
from crew_health.plans.domain.availability import (
    EnvContext,
    StepAssessment,
    assess_step,
    required_skills,
    runnable_now,
)
from crew_health.plans.domain.model import Plan, Step
from crew_health.plans.domain.run import RunRecheck


@dataclass(frozen=True)
class StepViews:
    world: PlansWorld
    view: View
    copy: UiCopy
    plan: Plan
    context: EnvContext
    origin: Literal["LIVE", "REPLAY", "SIM"] | None
    show_people: bool
    release_id: str
    runnable: dict[str, bool] = field(default_factory=dict)

    def decision(self, step: Step) -> GuidanceDecision | None:
        if self.origin is None:
            return None
        return StepGate(self.world.guidance).decision(
            self.view,
            plan=self.plan,
            step=step,
            origin_mode=self.origin,
            subject_person_id=self.context.subject_person_id,
            release_id=self.release_id,
        )

    def availability(
        self, step: Step, assessment: StepAssessment, decision: GuidanceDecision | None
    ) -> tuple[str, str | None]:
        unknown = text(self.copy, "designed_states", "unknown")
        if decision is None or decision.unknown or assessment.unknown:
            return "not_aboard", unknown
        if decision.outcome == "show_with_substitute":
            return self.substituted(step, assessment, decision) or ("not_aboard", unknown)
        if decision.outcome != "show":
            return "not_aboard", unknown
        if assessment.availability == "substituted":
            sub = self.plan.step(step.substitute_step_id or "")
            sub_decision = None if sub is None else self.decision(sub)
            if sub is not None and sub_decision is not None and sub_decision.executable:
                return "substituted", self.copy.render(
                    text(self.copy, "plan_lines", "substituted"), {"step": sub.text}
                )
            return "not_aboard", unknown
        if assessment.availability == "needs_skill_holder":
            skill = next(iter(required_skills(step)), "")
            catalog = read_document(self.view.release, ConfigName.SKILLS_CATALOG)
            short = (
                next(
                    (
                        object_value(v).get("short_name")
                        for v in array_value(catalog.get("skills", []))
                        if object_value(v).get("skill_id") == skill
                    ),
                    None,
                )
                if catalog
                else None
            )
            if not isinstance(short, str):
                return "not_aboard", unknown
            return "needs_skill_holder", self.copy.render(
                text(self.copy, "plan_lines", "needs_skill_holder"), {"skill_short": short}
            )
        if assessment.availability == "not_aboard":
            return "not_aboard", text(self.copy, "plan_lines", "not_aboard")
        return ("aboard", None) if decision.executable else ("not_aboard", unknown)

    def substituted(
        self, step: Step, assessment: StepAssessment, decision: GuidanceDecision
    ) -> tuple[str, str | None] | None:
        """Guidance's canonical substitute is shown only when it is this step's configured substitute, with
        the same pinned content, and the domain agrees the substitute runs. The principal stays not runnable."""
        sub = self.plan.step(step.substitute_step_id or "")
        ref = decision.substitute
        if (
            sub is None
            or ref is None
            or assessment.availability != "substituted"
            or not decision.executable
            or ref.id != sub.step_id
            or ref.content_sha256 != sub.content_sha256
        ):
            return None
        return "substituted", self.copy.render(
            text(self.copy, "plan_lines", "substituted"), {"step": sub.text}
        )

    def step(self, step: Step) -> dict[str, object]:
        assessment, decision = assess_step(step, self.plan, self.context), self.decision(step)
        availability, words = self.availability(step, assessment, decision)
        eligible = (
            ()
            if decision is None or not decision.executable or availability != "aboard"
            else decision.who_can
        )
        names = {p.person_id: p.display_name for p in self.context.people or ()}
        order = {p.person_id: p.roster_order for p in self.context.people or ()}
        who_can = [
            {"person_id": p.person_id, "display_name": names[p.person_id], "reason_code": p.reason_code}
            for p in sorted(assessment.who_can, key=lambda p: (order[p.person_id], p.person_id))
            if self.show_people and p.person_id in eligible and names.get(p.person_id)
        ]
        self.runnable[step.step_id] = (
            runnable_now(assessment) and decision is not None and decision.executable and bool(who_can)
        )
        quote, recheck = step.replaces_ground_task, step.recheck
        return {
            "step_id": step.step_id,
            "text": step.text,
            "text_first_time": step.text_first_time,
            "who": "skill:" + (step.who.skill_id or "") if step.who.kind == "skill" else step.who.kind,
            "availability": availability,
            "availability_text": words,
            "missing": {
                "instruments": list(assessment.missing_instruments),
                "supplies": list(assessment.missing_supplies),
                "skills": list(assessment.missing_skills),
            },
            "substitute_step_id": step.substitute_step_id,
            "substitute_only": step.substitute_only,
            "who_can": who_can,
            "body_region": step.body_region,
            "landmark_id": step.landmark_id,
            "guided_check_skill_id": step.guided_check_skill_id,
            "cannot_replace_text": step.cannot_replace_text,
            "recheck": None
            if recheck is None
            else {
                "cadence": recheck.cadence,
                "interval_seconds": recheck.interval_seconds,
                "basis": recheck.basis,
                "to_be_set_by": recheck.to_be_set_by,
                "text": due_text(self.world, self.copy, self.plan, RunRecheck(step.step_id, None, None, ())),
            },
            "opens_help": step.opens_help,
            "opens_flow_id": step.opens_flow_id,
            "medicine": None if step.medicine_named_as is None else {"named_as": step.medicine_named_as},
            "replaces_ground_task": None
            if quote is None
            else {"quote": quote.quote, "source_id": quote.source_id, "page": quote.page},
            "supply_cover_after": [
                {"supply_id": sid, "uses_left": cover} for sid, cover in assessment.cover_after
            ],
            "source_ids": list(step.source_ids),
            "basis": step.basis,
            "review_state": step.review_state,
        }

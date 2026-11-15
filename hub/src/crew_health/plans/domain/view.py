"""C22 planning and view projection; no state or clock reads."""

from dataclasses import dataclass

from crew_health.plans.domain.availability import (
    EnvContext,
    equipment_missing,
    holders,
    required_skills,
    step_applies,
)
from crew_health.plans.domain.availability import return_applies as return_applies
from crew_health.plans.domain.model import STAGE_ORDER, Plan, StageId, Step


@dataclass(frozen=True, slots=True)
class ViewStage:
    stage_id: StageId
    steps: tuple[Step, ...]


def equipment_here(step: Step, ctx: EnvContext) -> bool:
    return equipment_missing(step, ctx) == ((), (), ())


def _crew_equipment(step: Step, ctx: EnvContext) -> bool:
    return step.who.kind == "any_crew" and not step.needs.skills and equipment_here(step, ctx)


def plan_aboard(plan: Plan, ctx: EnvContext) -> bool:
    if ctx.environment_id not in plan.environments:
        return False
    applicable = tuple(s for s in plan.steps if step_applies(s, plan, ctx.environment_id))
    monitoring = any(s.stage_id in ("watch", "check_another_way") for s in applicable)
    for step in applicable:
        if step.substitute_only or step.stage_id not in ("check_another_way", "do_now", "watch"):
            continue
        if step.who.kind != "any_crew" or step.needs.skills:
            continue
        sub = plan.step(step.substitute_step_id) if step.substitute_step_id else None
        if _crew_equipment(step, ctx) or (sub is not None and _crew_equipment(sub, ctx)):
            return monitoring
    return False


def needs_medical_officer(step: Step, ctx: EnvContext) -> bool:
    if step.who.kind == "crew_medical_officer":
        return True
    officers = {p.person_id for p in ctx.people or () if p.officer_rank is not None}
    for skill in required_skills(step):
        eligible = holders(skill, ctx)
        if eligible and eligible <= officers:
            return True
    return False


def waits_for_medical_officer(plan: Plan, ctx: EnvContext) -> bool:
    considered = tuple(
        s
        for s in plan.steps
        if not s.substitute_only
        and s.stage_id in ("check_another_way", "do_now", "watch", "medical_officer")
        and step_applies(s, plan, ctx.environment_id)
        and equipment_here(s, ctx)
    )
    return bool(considered) and all(needs_medical_officer(s, ctx) for s in considered)


def view_stages(plan: Plan, ctx: EnvContext) -> tuple[ViewStage, ...]:
    stages = []
    for kind in STAGE_ORDER:
        if kind == "ground_log":
            stages.append(ViewStage(kind, ()))
        elif kind == "return":
            if return_applies(plan, ctx) and plan.return_stage is not None:
                stages.append(
                    ViewStage(
                        kind,
                        tuple(
                            s for s in plan.return_stage.prepare if step_applies(s, plan, ctx.environment_id)
                        ),
                    )
                )
        else:
            steps = tuple(
                s for s in plan.steps if s.stage_id == kind and step_applies(s, plan, ctx.environment_id)
            )
            if steps:
                stages.append(ViewStage(kind, steps))
    return tuple(stages)

"""E28-06 ordered deterministic guards on model proposals."""

from collections.abc import Sequence
from typing import Literal

from crew_health.plans.domain.model import Plan
from crew_health.plans.domain.text_check import find_amounts

ToolReason = Literal[
    "plan_not_active",
    "step_not_in_plan",
    "step_substitute_only",
    "step_not_runnable",
    "answer_out_of_scope",
    "dose_in_text",
]


def explain_step_reason(*, plan_active: bool, plan: Plan | None, step_id: str) -> ToolReason | None:
    if not plan_active or plan is None:
        return "plan_not_active"
    if plan.step(step_id) is None:
        return "step_not_in_plan"
    return None


def choose_plan_step_reason(
    *,
    plan_active: bool,
    plan: Plan | None,
    step_id: str,
    actionable: bool,
    runnable: bool,
    answer_in_scope: bool,
    string_arguments: Sequence[str],
) -> ToolReason | None:
    reason = explain_step_reason(plan_active=plan_active, plan=plan, step_id=step_id)
    if reason is not None:
        return reason
    step = plan.step(step_id) if plan is not None else None
    if step is not None and step.substitute_only and not actionable:
        return "step_substitute_only"
    if not runnable:
        return "step_not_runnable"
    if not answer_in_scope:
        return "answer_out_of_scope"
    if any(find_amounts(text) for text in string_arguments):
        return "dose_in_text"
    return None

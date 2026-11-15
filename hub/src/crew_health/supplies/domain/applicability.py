"""Which plan step needs count for cover in an environment (E32 3.3 INV-S1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from crew_health.supplies.domain.document import Plan, PlanStep
from crew_health.supplies.domain.model import Need


def step_applies(plan: Plan, step: PlanStep, environment_id: str) -> bool:
    """The plan's environments contain E and the step's environments are null or contain E; rejected plans never
    count. Every stage counts, return preparation included."""
    if plan.rejected or environment_id not in plan.environments:
        return False
    return step.environments is None or environment_id in step.environments


def applicable_needs(
    resolved: Sequence[tuple[Plan, PlanStep, tuple[Need, ...]]], environment_id: str
) -> Mapping[str, tuple[Need, ...]]:
    """Needs per supply in plan order then step order (the `used_by` order of E32 6.1)."""
    out: dict[str, list[Need]] = {}
    for plan, step, needs in resolved:
        if not step_applies(plan, step, environment_id):
            continue
        for need in needs:
            out.setdefault(need.supply_id, []).append(need)
    return {supply_id: tuple(needs) for supply_id, needs in out.items()}

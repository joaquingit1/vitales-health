"""C22 equipment, skill and performer assessments with unknowns kept explicit."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from crew_health.kernel.units import Quantity
from crew_health.plans.domain.model import Plan, Step

Availability = Literal["aboard", "not_aboard", "substituted", "needs_skill_holder"]
ReasonCode = Literal["any_crew", "skill_holder", "medical_officer"]


@dataclass(frozen=True, slots=True)
class Person:
    person_id: str
    display_name: str | None
    roster_order: int
    officer_rank: int | None
    available: bool
    skill_freshness: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class StockLevel:
    supply_id: str
    aboard: bool
    quantity_now: Quantity | None


@dataclass(frozen=True, slots=True)
class EnvContext:
    environment_id: str
    kit: frozenset[str] | None
    stock: Mapping[str, StockLevel] | None
    people: tuple[Person, ...] | None
    subject_person_id: str | None
    cover_after: Mapping[tuple[str, str], int | None]
    return_option_present: bool


@dataclass(frozen=True, slots=True)
class WhoCan:
    person_id: str
    display_name: str | None
    reason_code: ReasonCode


@dataclass(frozen=True, slots=True)
class StepAssessment:
    step_id: str
    applies: bool
    availability: Availability
    missing_instruments: tuple[str, ...]
    missing_supplies: tuple[str, ...]
    missing_skills: tuple[str, ...]
    unknown: tuple[str, ...]
    who_can: tuple[WhoCan, ...]
    substitute_step_id: str | None
    cover_after: tuple[tuple[str, int | None], ...]


def step_applies(step: Step, plan: Plan, environment_id: str) -> bool:
    return environment_id in plan.environments and (
        step.environments is None or environment_id in step.environments
    )


def return_applies(plan: Plan, ctx: EnvContext) -> bool:
    """EI11: a return stage only where the active environment's return option is established and listed."""
    return (
        ctx.return_option_present
        and plan.return_stage is not None
        and ctx.environment_id in plan.return_stage.environments
        and ctx.environment_id in plan.environments
    )


def equipment_missing(
    step: Step, ctx: EnvContext
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    missing_instruments = tuple(i for i in step.needs.instruments if ctx.kit is None or i not in ctx.kit)
    missing_supplies: list[str] = []
    unknown: set[str] = set()
    if ctx.kit is None and step.needs.instruments:
        unknown.add("kit_unknown")
    if ctx.stock is None and step.needs.supplies:
        unknown.add("stock_unknown")
    for need in step.needs.supplies:
        row = ctx.stock.get(need.supply_id) if ctx.stock is not None else None
        missing = not need.known or row is None or not row.aboard
        quantity = row.quantity_now if row is not None else None
        if quantity is not None:
            missing = missing or quantity.value <= 0
        if need.consumable and need.per_use is not None and quantity is not None:
            if quantity.unit != need.per_use.unit:
                missing = True
                unknown.add("unit_mismatch:" + need.supply_id)
            elif quantity.value < need.per_use.value:
                missing = True
        if missing:
            missing_supplies.append(need.supply_id)
    return missing_instruments, tuple(missing_supplies), tuple(sorted(unknown))


def holders(skill_id: str, ctx: EnvContext) -> frozenset[str]:
    # C22 rule 3: availability affects who_can, not whether the skill is aboard.
    return frozenset(
        p.person_id
        for p in ctx.people or ()
        if p.person_id != ctx.subject_person_id and p.skill_freshness.get(skill_id) in ("fresh", "due")
    )


def required_skills(step: Step) -> tuple[str, ...]:
    skills = step.needs.skills + (
        (step.who.skill_id,) if step.who.kind == "skill" and step.who.skill_id else ()
    )
    return tuple(dict.fromkeys(skills))


def _who_can(step: Step, ctx: EnvContext) -> tuple[WhoCan, ...]:
    people = [p for p in ctx.people or () if p.available]
    reason: ReasonCode
    if step.who.kind == "crew_medical_officer":
        people = [p for p in people if p.officer_rank is not None and p.person_id != ctx.subject_person_id]
        people.sort(
            key=lambda p: (p.officer_rank if p.officer_rank is not None else 0, p.roster_order, p.person_id)
        )
        reason = "medical_officer"
    else:
        if step.who.kind == "skill":
            eligible = holders(step.who.skill_id or "", ctx)
            people = [p for p in people if p.person_id in eligible]
            reason = "skill_holder"
        else:
            reason = "any_crew"
        people.sort(key=lambda p: (p.roster_order, p.person_id))
    return tuple(WhoCan(p.person_id, p.display_name, reason) for p in people)


def assess_step(step: Step, plan: Plan, ctx: EnvContext) -> StepAssessment:
    instruments, supplies, unknown_equipment = equipment_missing(step, ctx)
    missing_skills = tuple(s for s in required_skills(step) if not holders(s, ctx))
    unknown = set(unknown_equipment)
    if ctx.people is None:
        unknown.add("people_unknown")
    availability: Availability = "aboard"
    if instruments or supplies:
        sub = plan.step(step.substitute_step_id) if step.substitute_step_id else None
        # "substituted" promises that the substitute runs instead, so it needs its equipment and its skill holders.
        sub_runs = (
            sub is not None
            and equipment_missing(sub, ctx) == ((), (), ())
            and all(holders(skill, ctx) for skill in required_skills(sub))
        )
        availability = "substituted" if sub_runs else "not_aboard"
    elif missing_skills:
        availability = "needs_skill_holder"
    cover = tuple(
        (n.supply_id, ctx.cover_after.get((step.step_id, n.supply_id)) if n.per_use is not None else None)
        for n in step.needs.supplies
        if n.consumable
    )
    return StepAssessment(
        step.step_id,
        # A prepare step applies only while its return stage does (review item 3, design 3.5).
        step_applies(step, plan, ctx.environment_id)
        and (step.stage_id != "return" or return_applies(plan, ctx)),
        availability,
        instruments,
        supplies,
        missing_skills,
        tuple(sorted(unknown)),
        _who_can(step, ctx),
        step.substitute_step_id,
        cover,
    )


def runnable_now(assessment: StepAssessment) -> bool:
    return (
        assessment.applies
        and assessment.availability == "aboard"
        and bool(assessment.who_can)
        and not assessment.unknown
    )

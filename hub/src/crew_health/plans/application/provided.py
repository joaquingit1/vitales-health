"""Snapshot-bound projections for roles, teach, measurement, kitmap and catch-up."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from crew_health.catchup.application.ports import PlanCatchUpInputs
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, SessionContext
from crew_health.plans.application.act import PlanEscalationIntents
from crew_health.plans.application.kitmap_copies import KitMapContext, PlanChange, PlanColumns, PlanningDelta
from crew_health.plans.application.measurement_copies import GapPlan
from crew_health.plans.application.ports import (
    PlanEscalation,
    RunContext,
    SupplyNeed,
)
from crew_health.plans.application.teach_copies import GuidedStepRef
from crew_health.plans.application.world import PlansWorld
from crew_health.plans.domain.availability import (
    EnvContext,
    StockLevel,
    required_skills,
    step_applies,
)
from crew_health.plans.domain.errors import UnitMismatch
from crew_health.plans.domain.model import Plan, all_steps
from crew_health.plans.domain.view import plan_aboard, waits_for_medical_officer
from crew_health.roles.application import ports as roles_ports


def _needs(plan: Plan) -> dict[str, tuple[SupplyNeed, ...]]:
    return {
        step.step_id: tuple(
            SupplyNeed(need.plan_id, need.step_id, need.supply_id, need.per_use)
            for need in step.needs.supplies
        )
        for step in all_steps(plan)
    }


def _stock_added(context: EnvContext, delta: PlanningDelta) -> EnvContext:
    stock = dict(context.stock or {})
    for addition in delta.add_supplies:
        old = stock.get(addition.supply_id)
        if old is not None and old.quantity_now is not None:
            if old.quantity_now.unit != addition.quantity.unit:
                raise UnitMismatch(addition.supply_id)
            quantity = Quantity(old.quantity_now.value + addition.quantity.value, addition.quantity.unit)
        elif old is not None and old.aboard:
            # Adding a measured quantity cannot establish the unknown starting total.
            quantity = None
        else:
            quantity = addition.quantity
        stock[addition.supply_id] = StockLevel(addition.supply_id, True, quantity)
    return replace(context, stock=stock)


def _skills_added(context: EnvContext, delta: PlanningDelta) -> EnvContext:
    people = {person.person_id: person for person in context.people or ()}
    for addition in delta.add_skills:
        for person_id in addition.person_ids:
            person = people.get(person_id)
            if person is None:
                # C21's hypothetical holder is explicit in the planning request; it does not create a crew member.
                continue
            people[person_id] = replace(
                person, skill_freshness={**person.skill_freshness, addition.skill_id: "fresh"}
            )
    return replace(context, people=tuple(people.values()) if context.people is not None else None)


class PlansProvided:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def read(self, snapshot: ReadSnapshot, session: SessionContext, escalation_id: str) -> PlanEscalation:
        return PlanEscalationIntents(self.world).read(snapshot, session, escalation_id)

    def state(self, plan_run_id: str) -> roles_ports.PlanRunSnapshot | None:
        with self.world.snapshots.snapshot() as view:
            run = self.world.repository.load(view, plan_run_id)
            if run is None:
                return None
            pins = dict(run.pins)
            if "role-rules" not in pins or "skills-catalog" not in pins:
                return None
            plan, restricted = self.world.plan(view, run)
            skills = (
                ()
                if restricted
                else tuple(
                    (step.step_id, skill)
                    for step in run.steps
                    if step.state == "open" and (content := plan.step(step.step_id)) is not None
                    for skill in required_skills(content)
                )
            )
            return roles_ports.PlanRunSnapshot(
                run.plan_run_id,
                run.subject_person_id,
                "closed" if run.state == "closed" else "open",
                skills,
                pins["role-rules"],
                pins["skills-catalog"],
            )

    def guided_step(self, plan_run_id: str, skill_id: str) -> GuidedStepRef | None:
        with self.world.snapshots.snapshot() as view:
            run = self.world.repository.load(view, plan_run_id)
            if run is None:
                return None
            plan, restricted = self.world.plan(view, run)
            pins = dict(run.pins)
            if "skills-catalog" not in pins or "ui-copy" not in pins:
                return None
            mission = self.world.mission.current(view) if self.world.mission is not None else None
            if mission is None:
                return None
            context = self.world.action_context(view, run)
            for step in run.steps:
                content = plan.step(step.step_id)
                if step.state == "open" and content is not None and content.guided_check_skill_id == skill_id:
                    return GuidedStepRef(
                        run.plan_run_id,
                        run.subject_person_id,
                        run.episode_id,
                        step.step_id,
                        mission.active_environment_id,
                        pins["skills-catalog"],
                        pins["ui-copy"],
                        run.state != "closed",
                        run.state != "closed"
                        and not restricted
                        and context.runnable.get(step.step_id, False),
                    )
            return None

    def context(self, session: SessionContext, plan_run_id: str) -> RunContext | None:
        with self.world.snapshots.snapshot() as view:
            run = self.world.repository.load(view, plan_run_id)
            if run is None or session.kind != "crew" or session.person_id != run.subject_person_id:
                return None
            mission = self.world.mission.current(view) if self.world.mission is not None else None
            if mission is None:
                return None
            plan, _ = self.world.plan(view, run)
            return RunContext(True, run.plan_id, mission.active_environment_id, run.origin_mode, _needs(plan))

    def plan_for(
        self, *, condition_ids: Sequence[str], environment_id: str, subject_person_id: str | None
    ) -> GapPlan | None:
        with self.world.snapshots.snapshot() as view:
            document = self.world.catalog.document(view.release)
            if document is None:
                return None
            for condition_id in condition_ids:
                for plan in document.plans:
                    if plan.condition_id != condition_id or environment_id not in plan.environments:
                        continue
                    steps = tuple(
                        s.step_id
                        for s in plan.steps
                        if s.stage_id == "check_another_way"
                        and not s.substitute_only
                        and step_applies(s, plan, environment_id)
                    )
                    if steps:
                        return GapPlan(plan.plan_id, steps)
            return None

    def row_columns(
        self, ctx: KitMapContext, *, kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> Sequence[PlanColumns]:
        document = self.world.catalog.document(ctx.snapshot.release)
        stock = None
        if self.world.stock is not None:
            try:
                stock = {
                    sid: StockLevel(row.supply_id, row.aboard, row.quantity_now)
                    for sid, row in self.world.stock.stock(ctx.environment_id).items()
                }
            except ProblemError:
                raise
            except Exception:
                stock = None
        context = EnvContext(ctx.environment_id, kit, stock, None, None, {}, False)
        result: list[PlanColumns] = []
        for plan_id in plan_ids:
            plan = document.plan(plan_id) if document is not None and plan_id is not None else None
            result.append(
                PlanColumns(plan_id, plan.written_for, plan_aboard(plan, context))
                if plan is not None
                else PlanColumns(plan_id, None, False)
            )
        return tuple(result)

    def plans_changed(
        self, ctx: KitMapContext, *, base_kit: frozenset[str], delta: PlanningDelta
    ) -> Sequence[PlanChange]:
        document = self.world.catalog.document(ctx.snapshot.release)
        if document is None:
            return ()
        needs = tuple(n for p in document.plans for s in all_steps(p) for n in s.needs.supplies)
        before = replace(self.world.environment(ctx.snapshot, ctx.environment_id, None, needs), kit=base_kit)
        after = _skills_added(
            _stock_added(replace(before, kit=base_kit | frozenset(delta.add_instruments)), delta), delta
        )
        changes: list[PlanChange] = []
        for plan in document.plans:
            if ctx.environment_id not in plan.environments:
                continue
            old = plan_aboard(plan, before), waits_for_medical_officer(plan, before)
            new = plan_aboard(plan, after), waits_for_medical_officer(plan, after)
            if old != new:
                changes.append(PlanChange(plan.plan_id, plan.condition_id, plan.flow_id, *old, *new))
        return tuple(changes)

    def catch_up_inputs(self, plan_run_id: str) -> PlanCatchUpInputs | None:
        with self.world.snapshots.snapshot() as view:
            run = self.world.repository.load(view, plan_run_id)
            if run is None:
                return None
            plan, _ = self.world.plan(view, run)
            wire = self.world.wire(view, run)
            rechecks = wire.get("rechecks")
            due: dict[str, str | None] = {}
            if isinstance(rechecks, list):
                for recheck in rechecks:
                    if isinstance(recheck, dict) and isinstance(recheck.get("step_id"), str):
                        text = recheck.get("due_text")
                        due[recheck["step_id"]] = text if isinstance(text, str) else None
            done: list[tuple[datetime, str]] = []
            watch: list[tuple[str, str]] = []
            replaces: list[str] = []
            for step in run.steps:
                content = plan.step(step.step_id)
                if content is None:
                    continue
                if (
                    step.state == "done"
                    and step.stage_id in ("check_another_way", "do_now")
                    and step.at is not None
                ):
                    done.append((step.at, content.text))
                if step.stage_id == "watch" and not step.substitute_only:
                    watch.append((content.text, due.get(step.step_id) or ""))
                if content.replaces_ground_task is not None:
                    replaces.append(content.replaces_ground_task.quote)
            return PlanCatchUpInputs(
                tuple(done),
                tuple(watch),
                tuple(replaces),
                tuple(item.text for item in plan.ground_log_items),
            )

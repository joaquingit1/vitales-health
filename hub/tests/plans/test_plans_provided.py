"""Consumer seam compatibility and pure projections over real plan storage."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ConfigName
from crew_health.guidance.application.ports import Fact, Known, OperatorFact
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshot
from crew_health.kitmap.application import ports as real_kitmap
from crew_health.measurement.application import ports as real_measurement
from crew_health.plans.application import kitmap_copies, measurement_copies
from crew_health.plans.application.kitmap_copies import (
    KitMapContext,
    PlanningDelta,
    SkillAddition,
    SupplyAddition,
)
from crew_health.plans.application.provided import PlansProvided
from crew_health.plans.domain.run import Closure, Escalation, derive_state
from crew_health.plans.module import register
from crew_health.roles.application import ports as roles_ports

from .plans_stack import NOW, PlansStack, obj, rows, session, stack


@dataclass(frozen=True)
class ReleaseView:
    release_id: str
    has_onboard_plans: bool = True


@pytest.mark.trace("T28-30", "A1", "D9")
def test_registration_uses_roles_real_protocol_key() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    assert roles_ports.PlanRunReader in registry.providers


@pytest.mark.trace("T28-42", "A1")
def test_structural_copies_match_consumer_signatures_and_field_order() -> None:
    pairs = (
        (measurement_copies.GapPlanPort, real_measurement.GapPlanPort, ("plan_for",)),
        (kitmap_copies.PlanColumnsPort, real_kitmap.PlanColumnsPort, ("row_columns", "plans_changed")),
    )
    for copy, real, methods in pairs:
        for method in methods:
            assert inspect.signature(getattr(copy, method)) == inspect.signature(getattr(real, method))
    for copy_dataclass, real_dataclass in (
        (measurement_copies.GapPlan, real_measurement.GapPlan),
        (kitmap_copies.PlanColumns, real_kitmap.PlanColumns),
        (kitmap_copies.PlanChange, real_kitmap.PlanChange),
        (kitmap_copies.PlanningDelta, real_kitmap.PlanningDelta),
        (kitmap_copies.SupplyAddition, real_kitmap.SupplyAddition),
        (kitmap_copies.SkillAddition, real_kitmap.SkillAddition),
    ):
        assert is_dataclass(copy_dataclass) and is_dataclass(real_dataclass)
        assert tuple(f.name for f in fields(copy_dataclass)) == tuple(f.name for f in fields(real_dataclass))


@pytest.mark.trace("T28-12", "T28-30", "T28-42")
def test_roles_state_maps_all_open_states_and_requires_real_pins(tmp_path: Path) -> None:
    s = stack(tmp_path)
    wire = s.start()
    run_id = str(wire["plan_run_id"])
    provided = PlansProvided(s.world)
    with s.db.snapshot() as view:
        original = s.world.load(view, run_id)
    for state in ("active", "watching", "escalated", "closed"):
        run = original
        if state == "watching":
            run = replace(
                run,
                steps=tuple(
                    replace(step, state="done") if step.stage_id in ("check_another_way", "do_now") else step
                    for step in run.steps
                ),
            )
        if state == "escalated":
            run = replace(run, escalations=(Escalation("icl10_worse_chest", "hlp_test", NOW, False, None),))
        if state == "closed":
            run = replace(run, close=Closure("resolved_on_board", None, s.session.actor, NOW))
        run = replace(run, state=derive_state(run))
        with s.db.begin(actor=s.session.actor, correlation_id="cor_state") as uow:
            s.world.repository.save(uow, run)
        snapshot = provided.state(run_id)
        assert snapshot is not None
        assert snapshot.state == ("closed" if state == "closed" else "open")
    with s.db.begin(actor=s.session.actor, correlation_id="cor_pin") as uow:
        uow.connection.execute(
            "DELETE FROM plans_run_pin WHERE plan_run_id=? AND name=?", (run_id, "role-rules")
        )
    assert provided.state(run_id) is None


@pytest.mark.trace("T28-05", "T28-07", "T28-42")
def test_guided_step_runnable_tracks_real_guidance_and_current_environment(tmp_path: Path) -> None:
    s = stack(tmp_path)
    wire = s.start()
    provided = PlansProvided(s.world)
    ref = provided.guided_step(str(wire["plan_run_id"]), "pulse_check_wrist")
    assert ref is not None and ref.step_runnable
    assert ref.environment_id == "lunar_surface"
    s.content.unknown = True
    ref = provided.guided_step(str(wire["plan_run_id"]), "pulse_check_wrist")
    assert ref is not None and not ref.step_runnable
    assert provided.guided_step(str(wire["plan_run_id"]), "missing_skill") is None
    s.mission.environment = None
    assert provided.guided_step(str(wire["plan_run_id"]), "pulse_check_wrist") is None


@pytest.mark.trace("T28-39", "T28-42")
def test_gap_plan_respects_condition_order_environment_and_substitute_filter(tmp_path: Path) -> None:
    s = stack(tmp_path)
    provided = PlansProvided(s.world)
    answer = provided.plan_for(
        condition_ids=("unknown", "ICL10"), environment_id="lunar_surface", subject_person_id=None
    )
    assert answer is not None
    assert answer.plan_id == "plan_icl10"
    assert answer.check_another_way_step_ids == ("icl10_pulse_by_crewmate",)
    assert provided.plan_for(condition_ids=(), environment_id="lunar_surface", subject_person_id=None) is None
    assert (
        provided.plan_for(condition_ids=("ICL10",), environment_id="unknown", subject_person_id=None) is None
    )


@pytest.mark.trace("T28-27", "T28-39")
def test_row_columns_preserves_every_input_entry_and_order(tmp_path: Path) -> None:
    s = stack(tmp_path)
    provided = PlansProvided(s.world)
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        columns = provided.row_columns(ctx, kit=frozenset(), plan_ids=(None, "unknown", "plan_icl10", None))
    assert tuple(c.plan_id for c in columns) == (None, "unknown", "plan_icl10", None)
    assert columns[0] == kitmap_copies.PlanColumns(None, None, False)
    assert columns[1] == kitmap_copies.PlanColumns("unknown", None, False)
    assert columns[2].plan_aboard
    assert columns[2].written_for == "condition"


def scratch_plan(s: PlansStack, requirement: str) -> None:
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(raw["plans"])[0]
    stage = rows(plan["stages"])[0]
    step = rows(stage["steps"])[0]
    step["who"] = "any_crew"
    step["guided_check_skill_id"] = None
    step["needs"] = {
        "instruments": ["scratch_instrument"] if requirement == "instrument" else [],
        "skills": [],
        "supplies": [
            {"supply_id": "eye_rinse_water", "per_use": 1, "basis": "team default", "to_be_set_by": None}
        ]
        if requirement == "supply"
        else [],
    }
    step["substitute_step_id"] = None
    stage["steps"] = [step]
    plan["stages"] = [stage, rows(plan["stages"])[-1]]
    raw["plans"] = [plan]
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)


@pytest.mark.trace("T28-27", "T28-39")
@pytest.mark.parametrize("requirement", ["instrument", "supply"])
def test_planning_instrument_and_supply_additions_are_pure_deterministic_changes(
    tmp_path: Path, requirement: str
) -> None:
    s = stack(tmp_path)
    scratch_plan(s, requirement)
    provided = PlansProvided(s.world)
    delta = PlanningDelta(
        ("scratch_instrument",) if requirement == "instrument" else (),
        (SupplyAddition("eye_rinse_water", Quantity(Decimal(1), Unit("{rinse}"))),)
        if requirement == "supply"
        else (),
        (),
    )
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        before = view.connection.rows("SELECT count(*) FROM plans_run", (), limit=1)
        changes = provided.plans_changed(ctx, base_kit=frozenset(), delta=delta)
        assert provided.plans_changed(ctx, base_kit=frozenset(), delta=delta) == changes
        assert view.connection.rows("SELECT count(*) FROM plans_run", (), limit=1) == before
    assert len(changes) == 1
    assert not changes[0].before_plan_aboard and changes[0].after_plan_aboard
    assert s.events == []


class OfficerSkills:
    def __init__(self, officer: str, other: str) -> None:
        self.officer, self.other = officer, other

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return Known(
            (
                OperatorFact(self.officer, 0, True, True, False, 1, {"pulse_check_wrist": "fresh"}),
                OperatorFact(self.other, 1, True, True, False, None, {}),
            )
        )


@pytest.mark.trace("T28-27", "T28-39")
def test_skill_only_planning_delta_adds_a_fresh_non_officer_holder(tmp_path: Path) -> None:
    s = stack(tmp_path)
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(raw["plans"])[0]
    plan["stages"] = [rows(plan["stages"])[0], rows(plan["stages"])[-1]]
    raw["plans"] = [plan]
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    s.rebind(skills=OfficerSkills("tomas", "mara"))
    provided = PlansProvided(s.world)
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        changes = provided.plans_changed(
            ctx,
            base_kit=frozenset(),
            delta=PlanningDelta((), (), (SkillAddition("pulse_check_wrist", ("mara",)),)),
        )
    assert len(changes) == 1
    assert changes[0].before_waits_for_mo and not changes[0].after_waits_for_mo
    assert not changes[0].before_plan_aboard and not changes[0].after_plan_aboard


@pytest.mark.trace("T28-15", "T28-42", "D9")
def test_context_and_catch_up_come_from_the_pinned_run(tmp_path: Path) -> None:
    s = stack(tmp_path)
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    rows(rows(raw["plans"])[0]["stages"])[-1]["items"] = [
        {"text": "Keep the episode observations together.", "source_ids": []}
    ]
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    wire = s.start()
    provided = PlansProvided(s.world)
    run_id = str(wire["plan_run_id"])
    context = provided.context(s.session, run_id)
    assert context is not None and context.visible
    assert context.plan_id == "plan_icl10" and context.environment_id == "lunar_surface"
    assert provided.context(session("tomas"), run_id) is None
    catchup = provided.catch_up_inputs(run_id)
    assert catchup is not None
    assert catchup.watch_steps
    assert catchup.ground_log_items == ("Keep the episode observations together.",)

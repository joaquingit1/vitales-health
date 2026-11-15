"""Privacy and absent-fact regressions for consumer projections and release activation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.kernel.config import CandidateRelease, ConfigName
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, SessionContext
from crew_health.plans.application.audience import RunViewersRule
from crew_health.plans.application.kitmap_copies import (
    KitMapContext,
    PlanningDelta,
    SkillAddition,
    SupplyAddition,
)
from crew_health.plans.application.ports import StockLevel
from crew_health.plans.application.provided import PlansProvided
from crew_health.plans.application.release_check import PlansReleaseCheck
from crew_health.plans.domain.errors import UnitMismatch

from .plans_stack import NOW, obj, rows, session, stack
from .test_plans_provided import ReleaseView, scratch_plan
from .test_plans_release_check import (
    candidate_without_unverifiable_annotations,
    check_settings,
    edit_document,
)


def audience(run_id: str) -> Audience:
    return Audience("plans.run_viewers", run_id, AudienceProjectionKey("plans", "plan_run.changed", 1), "1")


@pytest.mark.trace("D9", "T28-15", "T28-30")
@pytest.mark.parametrize(
    "viewer,expected",
    [
        (session(), True),
        (session("tomas"), False),
        (session("tomas", officer=True), False),
        (SessionContext("ses_station", "station", Actor("station", "screen"), None, frozenset()), False),
        (SessionContext("ses_ground", "ground", Actor("ground", "ground"), None, frozenset()), False),
        (SessionContext("ses_no_person", "crew", Actor("person", "mara"), None, frozenset()), False),
    ],
)
def test_event_audience_admits_only_subject_crew_session(
    tmp_path: Path, viewer: SessionContext, expected: bool
) -> None:
    s = stack(tmp_path)
    run = s.start()
    with s.db.snapshot() as view:
        assert (
            RunViewersRule(s.world.repository).admits(view, viewer, audience(str(run["plan_run_id"])))
            is expected
        )
        assert not RunViewersRule(s.world.repository).admits(view, viewer, audience("prun_unknown"))


@pytest.mark.trace("D9", "T28-42")
def test_unknown_run_has_no_consumer_projection(tmp_path: Path) -> None:
    provided = PlansProvided(stack(tmp_path).world)
    assert provided.state("prun_unknown") is None
    assert provided.guided_step("prun_unknown", "pulse_check_wrist") is None
    assert provided.context(session(), "prun_unknown") is None
    assert provided.catch_up_inputs("prun_unknown") is None


@pytest.mark.trace("T28-17", "T28-42")
@pytest.mark.parametrize("missing", ["skills-catalog", "ui-copy"])
def test_missing_run_pin_never_produces_guided_step_defaults(tmp_path: Path, missing: str) -> None:
    s = stack(tmp_path)
    run = s.start()
    run_id = str(run["plan_run_id"])
    with s.db.begin(actor=s.session.actor, correlation_id="cor_missing_pin") as uow:
        uow.connection.execute("DELETE FROM plans_run_pin WHERE plan_run_id=? AND name=?", (run_id, missing))
    provided = PlansProvided(s.world)
    assert provided.guided_step(run_id, "pulse_check_wrist") is None
    if missing == "skills-catalog":
        assert provided.state(run_id) is None


@pytest.mark.trace("T28-08", "T28-42", "D9")
def test_context_has_no_default_environment_and_rejects_station_session(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    run_id = str(run["plan_run_id"])
    station = SessionContext("ses_station", "station", Actor("station", "screen"), "mara", frozenset())
    assert PlansProvided(s.world).context(station, run_id) is None
    s.mission.environment = None
    assert PlansProvided(s.world).context(s.session, run_id) is None
    without_mission = PlansProvided(replace(s.world, mission=None))
    assert without_mission.context(s.session, run_id) is None
    assert without_mission.guided_step(run_id, "pulse_check_wrist") is None


@pytest.mark.trace("T28-17", "T28-27", "T28-42")
def test_missing_active_document_cannot_propose_a_plan_or_planning_change(tmp_path: Path) -> None:
    s = stack(tmp_path)
    documents = dict(s.docs)
    del documents[ConfigName.ONBOARD_PLANS]
    s.release = MemoryRelease("release_without_plans", documents)
    provided = PlansProvided(s.world)
    assert (
        provided.plan_for(condition_ids=("ICL10",), environment_id="lunar_surface", subject_person_id="mara")
        is None
    )
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        columns = provided.row_columns(ctx, kit=frozenset(), plan_ids=("plan_icl10", None))
        assert len(columns) == 2
        assert all(not column.plan_aboard and column.written_for is None for column in columns)
        assert provided.plans_changed(ctx, base_kit=frozenset(), delta=PlanningDelta((), (), ())) == ()


@pytest.mark.trace("T28-17", "T28-42")
def test_restricted_state_does_not_advertise_open_skill_work(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    raw["plans"] = []
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    state = PlansProvided(s.world).state(str(run["plan_run_id"]))
    assert state is not None and state.state == "open"
    assert state.open_skill_steps == ()


@pytest.mark.trace("T28-27", "EI12", "AGENTS-5")
def test_planning_supply_addition_preserves_unknown_starting_quantity(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scratch_plan(s, "supply")
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    supplies = rows(obj(rows(rows(rows(raw["plans"])[0]["stages"])[0]["steps"])[0]["needs"])["supplies"])
    supplies[0]["per_use"] = 10
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    s.supplies.levels["eye_rinse_water"] = StockLevel("eye_rinse_water", True, None, Unit("{rinse}"))
    provided = PlansProvided(s.world)
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        result = provided.plans_changed(
            ctx,
            base_kit=frozenset(),
            delta=PlanningDelta(
                (), (SupplyAddition("eye_rinse_water", Quantity(Decimal(1), Unit("{rinse}"))),), ()
            ),
        )
    # A newly known increment cannot turn an unknown starting total into an insufficient measured total.
    assert result == ()
    assert s.supplies.levels["eye_rinse_water"].quantity_now is None
    assert s.events == []


@pytest.mark.trace("T28-27", "EI12", "UCUM")
def test_planning_adds_like_units_and_refuses_unit_mismatch_without_writes(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scratch_plan(s, "supply")
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    supplies = rows(obj(rows(rows(rows(raw["plans"])[0]["stages"])[0]["steps"])[0]["needs"])["supplies"])
    supplies[0]["per_use"] = 2
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    s.supplies.levels["eye_rinse_water"] = StockLevel(
        "eye_rinse_water", True, Quantity(Decimal(1), Unit("{rinse}")), Unit("{rinse}")
    )
    provided = PlansProvided(s.world)
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        changes = provided.plans_changed(
            ctx,
            base_kit=frozenset(),
            delta=PlanningDelta(
                (), (SupplyAddition("eye_rinse_water", Quantity(Decimal(1), Unit("{rinse}"))),), ()
            ),
        )
        assert len(changes) == 1 and not changes[0].before_plan_aboard and changes[0].after_plan_aboard
        with pytest.raises(UnitMismatch) as caught:
            provided.plans_changed(
                ctx,
                base_kit=frozenset(),
                delta=PlanningDelta(
                    (), (SupplyAddition("eye_rinse_water", Quantity(Decimal(1), Unit("mL"))),), ()
                ),
            )
        assert caught.value.supply_id == "eye_rinse_water"
    assert s.supplies.levels["eye_rinse_water"].quantity_now == Quantity(Decimal(1), Unit("{rinse}"))
    assert s.events == []


class FailedStock:
    def __init__(self, storage: bool) -> None:
        self.storage = storage

    def stock(self, environment_id: str) -> Mapping[str, StockLevel]:
        if self.storage:
            raise StorageUnavailable("io_error")
        raise RuntimeError("optional fact unavailable")


@pytest.mark.trace("T28-27", "T28-07", "EI12")
def test_row_columns_distinguishes_missing_optional_stock_from_storage_failure(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scratch_plan(s, "supply")
    for provider in (None, FailedStock(False)):
        provided = PlansProvided(replace(s.world, stock=provider))
        with s.db.snapshot() as view:
            ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
            columns = provided.row_columns(ctx, kit=frozenset(), plan_ids=("plan_icl10",))
            assert len(columns) == 1 and not columns[0].plan_aboard
    provided = PlansProvided(replace(s.world, stock=FailedStock(True)))
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "lunar_surface", NOW)
        with pytest.raises(StorageUnavailable):
            provided.row_columns(ctx, kit=frozenset(), plan_ids=("plan_icl10",))


@pytest.mark.trace("T28-27", "T28-39")
def test_planning_does_not_invent_missing_people_or_environment_coverage(tmp_path: Path) -> None:
    s = stack(tmp_path)
    provided = PlansProvided(replace(s.world, skills=None))
    with s.db.snapshot() as view:
        ctx = KitMapContext(view, ReleaseView(view.release.release_id), "unknown_environment", NOW)
        assert (
            provided.plans_changed(
                ctx,
                base_kit=frozenset(),
                delta=PlanningDelta((), (), (SkillAddition("pulse_check_wrist", ("unknown_person",)),)),
            )
            == ()
        )
    assert s.events == []


@pytest.mark.trace("T28-42", "R28-18")
def test_catchup_contains_completed_work_watch_due_words_and_pinned_ground_items(tmp_path: Path) -> None:
    s = stack(tmp_path)
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    rows(rows(raw["plans"])[0]["stages"])[-1]["items"] = [{"text": "Pinned ground entry", "source_ids": []}]
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    run = s.start()
    s.act(run, "step_done", step_id="icl10_note_episodes")
    supplied_plan = s.world.catalog.document(s.release)
    assert supplied_plan is not None
    plan = supplied_plan.plan("plan_icl10")
    assert plan is not None
    step = plan.step("icl10_note_episodes")
    assert step is not None
    catchup = PlansProvided(s.world).catch_up_inputs(str(run["plan_run_id"]))
    assert catchup is not None
    assert catchup.done_steps == ((NOW, step.text),)
    substitute = plan.step("icl10_pulse_at_oximeter")
    assert substitute is not None
    assert all(text != substitute.text for text, due in catchup.watch_steps)
    recheck_step = plan.step("icl10_pulse_at_cameras")
    assert recheck_step is not None and recheck_step.recheck is not None
    assert recheck_step.recheck.to_be_set_by is not None
    pulse = next(item for item in catchup.watch_steps if item[0] == recheck_step.text)
    assert pulse[1] and recheck_step.recheck.to_be_set_by in pulse[1]
    assert catchup.ground_log_items == ("Pinned ground entry",)


@pytest.mark.trace("T28-42", "D9")
def test_catchup_refuses_fabricated_names_when_roster_is_absent(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    with pytest.raises(ProblemError) as caught:
        PlansProvided(replace(s.world, roster=None)).catch_up_inputs(str(run["plan_run_id"]))
    assert caught.value.code is ErrorCode.INTERNAL_ERROR and caught.value.retryable


@pytest.mark.trace("T28-18", "EI4")
@pytest.mark.parametrize(
    "malformation",
    [
        "skills_not_array",
        "skill_not_object",
        "kits_not_object",
        "kit_not_array",
        "instrument_not_string",
        "missing_dependency",
        "settings_absent",
        "document_too_large",
    ],
)
def test_release_check_rejects_malformed_or_unverifiable_candidate_dependencies(malformation: str) -> None:
    candidate = candidate_without_unverifiable_annotations()
    settings = check_settings()
    name = ConfigName.SKILLS_CATALOG if malformation.startswith("skill") else ConfigName.KIT_MAP
    raw = obj(json.loads(candidate.documents[name].raw))
    if malformation == "skills_not_array":
        raw["skills"] = "untrusted"
    elif malformation == "skill_not_object":
        raw["skills"] = ["untrusted"]
    elif malformation == "kits_not_object":
        raw["kits"] = "untrusted"
    elif malformation == "kit_not_array":
        raw["kits"] = {"lunar_surface": "untrusted"}
    elif malformation == "instrument_not_string":
        raw["kits"] = {"lunar_surface": [7]}
    elif malformation == "missing_dependency":
        documents = dict(candidate.documents)
        del documents[ConfigName.RESPONDER_FLOWS]
        candidate = CandidateRelease(candidate.release_id, documents)
    elif malformation == "document_too_large":
        settings = replace(settings, max_document_bytes=1)
    if malformation not in ("missing_dependency", "settings_absent", "document_too_large"):
        candidate = edit_document(candidate, name, raw)
    check = PlansReleaseCheck(None if malformation == "settings_absent" else settings)
    findings = check.check(candidate)
    assert len(findings) == 1 and findings[0].document == ConfigName.ONBOARD_PLANS
    assert findings[0].message == "plans check failed"

"""Runnable guards, commuting actions and transactionally coupled supply use."""

from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.plan_run_schema import PlanRun
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.application.ports import StockLevel

from .plans_stack import PlansStack, obj, rows, run_step, stack


def plan_document(s: PlansStack) -> dict[str, object]:
    import json

    return obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))


def scratch_supply_step(s: PlansStack) -> None:
    document = plan_document(s)
    plan = rows(document["plans"])[0]
    step = rows(rows(plan["stages"])[1]["steps"])[0]
    obj(step["needs"])["supplies"] = [
        {"supply_id": "eye_rinse_water", "per_use": 1, "basis": "team default", "to_be_set_by": None}
    ]
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    s.supplies.levels["eye_rinse_water"] = StockLevel(
        "eye_rinse_water", True, Quantity(Decimal(5), Unit("{rinse}")), Unit("{rinse}")
    )


@pytest.mark.trace("T28-13")
def test_runnable_step_done_and_close_produce_valid_run_wire(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    done = s.act(run, "step_done", step_id="icl10_note_episodes")
    assert run_step(done, "icl10_note_episodes")["state"] == "done"
    assert done["revision"] == 2
    assert s.events[-1]["type"] == "plan_run.step_done"
    closed = s.act(done, "close", disposition="resolved_on_board")
    assert closed["state"] == "closed"
    assert closed["allowed_actions"] == []
    PlanRun.model_validate(closed)


@pytest.mark.trace("T28-02")
def test_missing_camera_requires_explicit_oximeter_substitute(tmp_path: Path) -> None:
    s = stack(tmp_path)
    import json

    kitmap = obj(json.loads(s.docs[ConfigName.KIT_MAP].raw))
    kits = obj(kitmap["kits"])
    kits["lunar_surface"] = [v for v in cast(list[str], kits["lunar_surface"]) if v != "camera_station"]
    s.replace_document(ConfigName.KIT_MAP, kitmap)
    run = s.start()
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_pulse_at_cameras")
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE
    substituted = s.act(run, "use_substitute", step_id="icl10_pulse_at_cameras")
    done = s.act(substituted, "step_done", step_id="icl10_pulse_at_oximeter")
    assert run_step(done, "icl10_pulse_at_cameras")["state"] == "substituted"
    assert run_step(done, "icl10_pulse_at_oximeter")["state"] == "done"


@pytest.mark.trace("T28-13", "SYSTEM-8.2")
def test_unknown_content_never_authorizes_step_or_writes_anything(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.content.unknown = True
    before = s.query("SELECT count(*) FROM platform_command_ledger")
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes")
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE
    assert s.runs.get(s.session, str(run["plan_run_id"]))["revision"] == 1
    assert s.query("SELECT count(*) FROM platform_command_ledger") == before
    assert len(s.events) == 1


@pytest.mark.trace("T28-13")
def test_stale_commuting_actions_merge_but_close_and_escalate_require_revision(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.act(run, "step_done", step_id="icl10_note_episodes")
    merged = s.act(run, "step_done", step_id="icl10_avoid_triggers", if_match=1)
    assert merged["revision"] == 3
    assert run_step(merged, "icl10_note_episodes")["state"] == "done"
    for action, extra in (
        ("close", {"disposition": "other"}),
        ("escalate", {"step_id": "icl10_worse_chest"}),
    ):
        with pytest.raises(ProblemError) as error:
            s.act(run, action, key="stale_" + action, if_match=1, **extra)
        assert error.value.code is ErrorCode.STALE_VERSION
    with pytest.raises(ProblemError) as error:
        s.actions.act(
            s.session,
            str(run["plan_run_id"]),
            {"action": "close", "disposition": "other", "client_time": "2026-11-14T13:37:00Z"},
            "missing_version",
            None,
        )
    assert error.value.code is ErrorCode.VERSION_REQUIRED


@pytest.mark.trace("T28-04", "EI12")
def test_supply_debit_commits_once_and_below_zero_rolls_back_everything(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scratch_supply_step(s)
    run = s.start()
    s.supplies.refuse = True
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes", key="debit")
    assert error.value.code is ErrorCode.SUPPLY_BELOW_ZERO
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((0,),)
    assert s.runs.get(s.session, str(run["plan_run_id"]))["revision"] == 1
    assert len(s.events) == 1
    s.supplies.refuse = False
    done = s.act(run, "step_done", step_id="icl10_note_episodes", key="debit")
    assert len(cast(list[str], run_step(done, "icl10_note_episodes")["supply_use_ids"])) == 1
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((1,),)
    assert s.act(run, "step_done", step_id="icl10_note_episodes", key="debit") == done
    assert len(s.events) == 2


@pytest.mark.trace("T28-08")
def test_active_environment_move_refuses_pinned_lunar_only_step(tmp_path: Path) -> None:
    s = stack(tmp_path)
    document = plan_document(s)
    step = rows(rows(rows(document["plans"])[0]["stages"])[1]["steps"])[0]
    step["environments"] = ["lunar_surface"]
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    run = s.start()
    s.mission.environment = "transit"
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes")
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE
    assert len(s.events) == 1


@pytest.mark.trace("T28-17", "R28-18")
def test_removed_plan_restricts_run_to_close_and_escalate(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    document = plan_document(s)
    document["plans"] = [p for p in rows(document["plans"]) if p["plan_id"] != "plan_icl10"]
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    restricted = s.runs.get(s.session, str(run["plan_run_id"]))
    assert set(cast(list[str], restricted["allowed_actions"])) == {"escalate", "close"}
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes")
    assert error.value.code is ErrorCode.INVALID_TRANSITION
    assert s.act(run, "close", disposition="other")["state"] == "closed"


@pytest.mark.trace("R28-22")
def test_observation_reference_without_reader_is_not_accepted(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.rebind(observations=None)
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes", observation_ids=["obs_1"])
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/observation_ids"
    assert len(s.events) == 1


@pytest.mark.trace("R28-22", "T28-06")
@pytest.mark.parametrize("mismatch", ["missing", "patient", "origin", "quality", "episode", "duplicate"])
def test_observations_must_match_subject_origin_quality_and_episode(tmp_path: Path, mismatch: str) -> None:
    from dataclasses import replace

    from crew_health.plans.application.ports import ObservationRef

    s = stack(tmp_path)
    run = s.start()
    record = ObservationRef("obs_valid", "mara", "ep_test", "SIM", "accepted")
    if mismatch == "patient":
        record = replace(record, patient_id="tomas")
    elif mismatch == "origin":
        record = replace(record, origin_mode="LIVE")
    elif mismatch == "quality":
        record = replace(record, quality_status="rejected")
    elif mismatch == "episode":
        record = replace(record, episode_id="ep_other")
    if mismatch != "missing":
        s.observations.records[record.observation_id] = record
    ids = [record.observation_id] * (2 if mismatch == "duplicate" else 1)
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes", observation_ids=ids)
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert s.runs.get(s.session, str(run["plan_run_id"]))["revision"] == 1
    assert len(s.events) == 1


@pytest.mark.trace("T28-08", "SYSTEM-8.2")
def test_missing_active_mission_prevents_step_execution(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.mission.environment = None
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes")
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE
    assert len(s.events) == 1


@pytest.mark.trace("T28-17", "R28-18", "PR-I9")
def test_corrupt_pin_and_removed_active_plan_still_allow_close_with_original_due_copy(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    original_due = {str(item["step_id"]): item["due_text"] for item in rows(run["rechecks"])}
    assert original_due
    with s.db.begin(actor=s.session.actor, correlation_id="cor_corrupt_pin") as uow:
        uow.connection.execute("UPDATE plans_parsed_plan SET sha256=?", ("0" * 64,))
    document = plan_document(s)
    document["plans"] = [p for p in rows(document["plans"]) if p["plan_id"] != run["plan_id"]]
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    closed = s.act(run, "close", disposition="other")
    assert closed["state"] == "closed"
    assert closed["allowed_actions"] == []
    assert {str(item["step_id"]): item["due_text"] for item in rows(closed["rechecks"])} == original_due
    assert s.query("SELECT state FROM plans_run") == (("closed",),)
    assert s.events[-1]["type"] == "plan_run.changed"


@pytest.mark.trace("T28-04", "T28-08", "R28-20")
def test_consumable_step_after_environment_move_debits_current_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Sequence
    from datetime import datetime

    from crew_health.kernel.uow import Actor, UnitOfWork
    from crew_health.plans.application.ports import SupplyNeed

    s = stack(tmp_path)
    scratch_supply_step(s)
    run = s.start()
    original = s.supplies.record_step_uses
    environments: list[str] = []

    def record(
        uow: UnitOfWork,
        *,
        environment_id: str,
        plan_run_id: str,
        step_id: str,
        occurrence: int,
        pinned_release_id: str,
        uses: Sequence[SupplyNeed],
        actor: Actor,
        at: datetime,
    ) -> Sequence[str]:
        environments.append(environment_id)
        return original(
            uow,
            environment_id=environment_id,
            plan_run_id=plan_run_id,
            step_id=step_id,
            occurrence=occurrence,
            pinned_release_id=pinned_release_id,
            uses=uses,
            actor=actor,
            at=at,
        )

    monkeypatch.setattr(s.supplies, "record_step_uses", record)
    s.mission.environment = "transit"
    done = s.act(run, "step_done", step_id="icl10_note_episodes")
    assert run_step(done, "icl10_note_episodes")["state"] == "done"
    assert environments == ["transit"]
    assert done["environment_id"] == "lunar_surface"
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((1,),)


@pytest.mark.trace("PR-I9", "R28-20", "AGENTS-8")
def test_active_supply_unit_edit_does_not_change_pinned_debit_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from collections.abc import Sequence
    from datetime import datetime

    from crew_health.kernel.uow import Actor, UnitOfWork
    from crew_health.plans.application.ports import SupplyNeed

    s = stack(tmp_path)
    scratch_supply_step(s)
    kitmap = obj(json.loads(s.docs[ConfigName.KIT_MAP].raw))
    supply = next(row for row in rows(kitmap["supplies"]) if row["supply_id"] == "eye_rinse_water")
    supply["unit"] = "pad"
    s.replace_document(ConfigName.KIT_MAP, kitmap)
    s.supplies.levels["eye_rinse_water"] = StockLevel(
        "eye_rinse_water", True, Quantity(Decimal(5), Unit("{pad}")), Unit("{pad}")
    )
    run = s.start()
    supply["unit"] = "set"
    s.replace_document(ConfigName.KIT_MAP, kitmap)
    original = s.supplies.record_step_uses
    amounts: list[Quantity | None] = []

    def record(
        uow: UnitOfWork,
        *,
        environment_id: str,
        plan_run_id: str,
        step_id: str,
        occurrence: int,
        pinned_release_id: str,
        uses: Sequence[SupplyNeed],
        actor: Actor,
        at: datetime,
    ) -> Sequence[str]:
        amounts.extend(need.per_use for need in uses)
        return original(
            uow,
            environment_id=environment_id,
            plan_run_id=plan_run_id,
            step_id=step_id,
            occurrence=occurrence,
            pinned_release_id=pinned_release_id,
            uses=uses,
            actor=actor,
            at=at,
        )

    monkeypatch.setattr(s.supplies, "record_step_uses", record)
    done = s.act(run, "step_done", step_id="icl10_note_episodes")
    assert run_step(done, "icl10_note_episodes")["state"] == "done"
    assert amounts == [Quantity(Decimal(1), Unit("{pad}"))]


@pytest.mark.trace("PR-I9", "T28-09")
def test_active_ui_copy_edit_does_not_change_open_run_due_text(tmp_path: Path) -> None:
    import json

    s = stack(tmp_path)
    run = s.start()
    original = rows(run["rechecks"])
    assert original
    copy = obj(json.loads(s.docs[ConfigName.UI_COPY].raw))
    obj(copy["plan_lines"])["recheck_pending"] = "Changed copy for {to_be_set_by}"
    s.replace_document(ConfigName.UI_COPY, copy)
    current = s.runs.get(s.session, str(run["plan_run_id"]))
    assert current["rechecks"] == original
    assert current["config_pins"] == run["config_pins"]


@pytest.mark.trace("PR-I9", "T28-09", "T28-14")
def test_active_calendar_edit_does_not_change_pinned_monthly_recheck(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timedelta

    from .plans_stack import NOW

    s = stack(tmp_path)
    document = plan_document(s)
    watch = rows(rows(rows(document["plans"])[0]["stages"])[2]["steps"])[0]
    obj(watch["recheck"])["cadence"] = "monthly"
    obj(watch["recheck"])["to_be_set_by"] = None
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    routine = obj(json.loads(s.docs[ConfigName.ROUTINE_RULES].raw))
    days = obj(routine["calendar"])["month_days"]
    assert isinstance(days, int)
    run = s.start()
    obj(routine["calendar"])["month_days"] = days + 1
    s.replace_document(ConfigName.ROUTINE_RULES, routine)
    done = s.act(run, "step_done", step_id="icl10_pulse_at_cameras")
    recheck = next(row for row in rows(done["rechecks"]) if row["step_id"] == "icl10_pulse_at_cameras")
    assert datetime.fromisoformat(str(recheck["due_at"])) == NOW + timedelta(days=days)

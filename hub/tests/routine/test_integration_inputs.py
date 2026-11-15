"""Producer inputs traverse real module wiring, SQLite outbox and durable delivery."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.evaluation import EvaluatorComputationFailure
from crew_health.routine.application.ports import MissionFacts

from .harness import RoutineHarness, instant, session, station


def at(hour: int, minute: int = 0, *, day: int = 1) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def audited(hub: RoutineHarness, code: str) -> bool:
    wanted = ('"event_type":"routine.' + code + '"').encode()
    return any(
        isinstance(row[0], bytes) and wanted in row[0]
        for row in hub.rows("SELECT envelope FROM platform_audit")
    )


def drain_timers(hub: RoutineHarness) -> None:
    for _ in range(
        hub.settings.integer("scheduler.max_jobs") // hub.settings.integer("scheduler.batch_size")
    ):
        if hub.dispatch() == 0:
            return
    raise RuntimeError("test scheduler failed to converge")


@pytest.mark.trace("T-E25-007", "D16", "V54")
def test_co2_location_members_reason_provenance_and_clear_restore_base(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    overrides = hub.rows(
        "SELECT override_id,reason_text,cadence_text,ends_kind FROM routine_override WHERE item_id='vision_self_check'"
    )
    assert len(overrides) == 1
    assert "CO2" in str(overrides[0][1]) and "Daily" in str(overrides[0][2])
    assert overrides[0][3] == "warning_cleared"
    assert hub.rows(
        "SELECT person_id,member_basis FROM routine_override_member WHERE override_id=? ORDER BY person_id",
        (overrides[0][0],),
    ) == (("person_mara", "location"), ("person_sofia", "location"), ("person_tomas", "location_unknown"))
    view = hub.queries.task(session(), "vision_self_check")
    assert view["effective_cadence"] == {
        "word": "daily",
        "n": None,
        "note": "while CO2 stays above the limit",
    }
    assert hub.rows(
        "SELECT source_ref,source_origin_mode FROM routine_override WHERE override_id=?", (overrides[0][0],)
    ) == (("wrn_co2_1", "SIM"),)
    hub.clear_warning()
    hub.deliver()
    assert hub.rows(
        "SELECT state,end_reason FROM routine_override WHERE override_id=?", (overrides[0][0],)
    ) == (("ended", "warning_cleared"),)
    assert (
        hub.queries.task(session(), "vision_self_check")["effective_cadence"]
        == hub.queries.task(session(), "vision_self_check")["base_cadence"]
    )


@pytest.mark.trace("T-E25-100", "T-E25-007")
def test_warning_superseded_repoints_and_config_changed_waits(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    before = hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0]
    hub.clear_warning(reason="superseded")
    hub.raise_warning("wrn_co2_2", previous_warning_id="wrn_co2_1")
    hub.deliver()
    assert hub.rows(
        "SELECT override_id,revision,source_ref,source_chain_json FROM routine_override WHERE item_id='vision_self_check'"
    ) == ((before, 2, "wrn_co2_2", '["wrn_co2_1","wrn_co2_2"]'),)
    hub.raise_warning("wrn_co2_2", previous_warning_id="wrn_co2_1")
    hub.deliver()
    assert hub.rows(
        "SELECT override_id,revision FROM routine_override WHERE item_id='vision_self_check'"
    ) == ((before, 2),)
    hub.clear_warning("wrn_co2_2", reason="config_changed")
    hub.deliver()
    assert hub.rows(
        "SELECT state,awaiting_disposition FROM routine_override WHERE override_id=?", (before,)
    ) == (("active", 1),)


@pytest.mark.trace("T-E25-029", "V72", "T-E25-092")
@pytest.mark.parametrize("origin", ("REPLAY", "SIM"))
def test_live_rejects_historical_warnings_and_assistant_never_starts_one(tmp_path: Path, origin: str) -> None:
    hub = station(tmp_path, at=at(9), mode="LIVE")
    hub.raise_warning(origin_mode=origin)
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "origin_rejected")
    hub.raise_warning("wrn_assistant_1", raised_by="assistant", origin_mode="LIVE")
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "assistant_input_ignored")


@pytest.mark.trace("T-E25-043", "D16")
def test_co2_initial_and_two_hour_repeat_questions_expire_partial(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    rows = hub.rows(
        "SELECT slot,due_from,state FROM routine_question_instance WHERE person_id=? ORDER BY due_from",
        ("person_mara",),
    )
    assert rows == (("initial", instant(at(9)), "open"), ("repeat:0", instant(at(11)), "open"))
    initial = hub.runtime.added_questions("person_mara", 1, at(9))
    repeated = hub.runtime.added_questions("person_mara", 1, at(11))
    assert initial and all(question.repeat_after_minutes is None for question in initial)
    assert any(question.repeat_after_minutes == 120 for question in repeated)
    instances = hub.engine.deps.store
    with hub.db.snapshot() as view:
        inst = instances.open_instances_for_person(view, "person_mara", 100)[0]
    hub.answer(inst.question_ids[0], answer_id="answer_partial")
    hub.advance(at(0, day=2))
    drain_timers(hub)
    assert hub.rows(
        "SELECT state FROM routine_question_instance WHERE person_id=? ORDER BY due_from", ("person_mara",)
    ) == (("partially_answered",), ("unanswered",))


@pytest.mark.trace("T-E25-005", "T-E25-091", "V74")
def test_sleep_holds_until_wake_and_old_overlay_revision_is_ignored(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(13))
    target = hub.rows(
        "SELECT occurrence_id,base_due_until FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check'",
        ("person_mara",),
    )[0]
    hub.overlay(started_at=at(13))
    hub.deliver()
    hub.advance(at(16))
    drain_timers(hub)
    assert hub.queries.occurrence(session(), str(target[0]))["suppressed_by_overlay"] == "sleep"
    assert hub.rows("SELECT state,due_until FROM routine_occurrence WHERE occurrence_id=?", (target[0],)) == (
        ("due", None),
    )
    hub.overlay(state="ended", revision=2, started_at=at(13), ended_at=at(16))
    hub.deliver()
    assert hub.rows("SELECT due_until FROM routine_occurrence WHERE occurrence_id=?", (target[0],)) == (
        (instant(at(18)),),
    )
    before = hub.rows("SELECT revision,due_until FROM routine_occurrence WHERE occurrence_id=?", (target[0],))
    hub.overlay(started_at=at(13), revision=1)
    hub.deliver()
    assert (
        hub.rows("SELECT revision,due_until FROM routine_occurrence WHERE occurrence_id=?", (target[0],))
        == before
    )


@pytest.mark.trace("T-E25-023", "T-E25-043", "D20")
def test_surface_walk_dust_warning_coalesces_into_existing_check(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(11, 40))
    hub.mission.facts = MissionFacts("mission_1", "SIM", "lunar_surface", None, "node_1")
    hub.overlay(
        "ovl_walk_1",
        person_id="person_ilya",
        kind="eva",
        state="ended",
        revision=2,
        started_at=at(9),
        ended_at=at(11, 40),
    )
    hub.deliver()
    checks = hub.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE item_id='dust_exposure_check' AND person_id=?",
        ("person_ilya",),
    )
    assert len(checks) == 1
    hub.advance(at(11, 55))
    hub.raise_warning("wrn_dust_1", "dust_lunar_act", location=None, environment_id="lunar_surface")
    hub.deliver()
    assert (
        hub.rows(
            "SELECT occurrence_id FROM routine_occurrence WHERE item_id='dust_exposure_check' AND person_id=?",
            ("person_ilya",),
        )
        == checks
    )
    assert hub.rows("SELECT COUNT(*) FROM routine_override_added WHERE target_id=?", (checks[0][0],)) == (
        (1,),
    )


@pytest.mark.trace("T-E25-096", "INV-E25-16")
def test_computation_fault_preserves_delivery_existing_state_and_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    before = hub.rows("SELECT override_id,revision,state FROM routine_override ORDER BY override_id")

    def fail(*args: object) -> None:
        raise EvaluatorComputationFailure("test_binding_failure")

    monkeypatch.setattr(hub.engine.inputs, "fire", fail)
    event = hub.raise_warning("wrn_co2_fail")
    hub.deliver()
    assert hub.rows("SELECT override_id,revision,state FROM routine_override ORDER BY override_id") == before
    with hub.db.snapshot() as view:
        assert hub.events.cursor(view.connection, "routine.inputs") >= event.seq
    assert any(component.code == "EVALUATION_UNAVAILABLE" for component in hub.health.snapshot().components)
    assert audited(hub, "evaluation_failed")


@pytest.mark.trace("T-E25-096", "INV-E25-16")
def test_storage_fault_aborts_delivery_and_does_not_advance_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = station(tmp_path, at=at(9))
    hub.deliver()
    with hub.db.snapshot() as view:
        position = hub.events.cursor(view.connection, "routine.inputs")

    def fail(*args: object) -> None:
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(hub.engine.inputs, "fire", fail)
    hub.raise_warning("wrn_co2_fail")
    with pytest.raises(StorageUnavailable, match="disk_full"):
        hub.deliver()
    with hub.db.snapshot() as view:
        assert hub.events.cursor(view.connection, "routine.inputs") == position
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)


@pytest.mark.trace("T-E25-091", "V74")
def test_ended_overlay_delivered_before_active_revision_gives_same_window(tmp_path: Path) -> None:
    forward = station(tmp_path / "forward", at=at(13))
    delayed = station(tmp_path / "delayed", at=at(13))
    forward.overlay(started_at=at(13))
    forward.deliver()
    forward.advance(at(16))
    delayed.advance(at(16))
    forward.overlay(state="ended", revision=2, started_at=at(13), ended_at=at(16))
    delayed.overlay(state="ended", revision=2, started_at=at(13), ended_at=at(16))
    delayed.overlay(started_at=at(13), revision=1)
    forward.deliver()
    delayed.deliver()
    query = "SELECT occurrence_id,due_until FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check'"
    assert forward.rows(query, ("person_mara",)) == delayed.rows(query, ("person_mara",))
    assert forward.rows(query, ("person_mara",))[0][1] == instant(at(18))


@pytest.mark.trace("T-E25-029", "INV-E25-16")
def test_person_binding_with_no_roster_fails_closed(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.engine.deps = replace(hub.engine.deps, roster=None)
    hub.engine.inputs.deps = hub.engine.deps
    hub.overlay(
        "ovl_finished",
        person_id="person_unknown",
        kind="eva",
        state="ended",
        revision=2,
        started_at=at(7),
        ended_at=at(9),
    )
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "roster_unavailable")

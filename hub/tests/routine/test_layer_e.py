"""Remaining hub design scenarios, exercised through the registered runtime."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.routine.adapters.sqlite_repository import SqliteRoutineStore
from crew_health.routine.application.ports import DailyCheckAbandoned, DailyCheckSubmitted, RosterMember
from crew_health.routine.domain.bindings import EnvironmentInput

from .harness import RoutineHarness, session, station
from .http_harness import HttpHarness, headers, problem
from .support import document
from .test_bounds import changed_rules, install
from .test_http_faults import activate, edited_release
from .test_integration_inputs import at, audited, drain_timers
from .test_query_acceptance import row_for
from .test_records_seams import Grants, evaluation, observation


@pytest.mark.trace("T-E25-003", "V53", "D21", "INV-02", "INV-E25-02")
def test_broken_cuff_skips_without_observation_and_preserves_weekly_slot(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence(item="blood_pressure")
    response = hub.client.post(
        f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
        json={"action": "skip", "skip_reason": "not_available", "note": "cuff broken"},
        headers=headers(key="broken_cuff", revision=1),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "skipped"
    assert response.json()["outcome"] is None
    assert response.json()["skip"]["reason"] == "not_available"
    assert hub.rows("SELECT count(*) FROM records_observation") == ((0,),)
    hub.advance(at(8, day=8))
    hub.engine.startup_sweep()
    following = row_for(hub, "blood_pressure")
    assert cast(dict[str, object], following["day"])["mission_day"] == 8


@pytest.mark.trace("T-E25-030", "UC03", "CONTRACTS 15")
def test_bound_override_cannot_be_stopped_by_an_officer(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    hub.raise_warning()
    hub.deliver()
    ref = str(hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0])
    response = hub.client.post(
        f"/v1/routine/overrides/{ref}/actions",
        json={"action": "end"},
        headers=headers("person_tomas", "crew_medical_officer", key="end_bound_rule", revision=1),
    )
    problem(response, "ACTION_NOT_ALLOWED", 403)
    assert hub.rows("SELECT state FROM routine_override WHERE override_id=?", (ref,)) == (("active",),)


def environment(hub: RoutineHarness, environment_id: str, *, activated_at: bool = True) -> None:
    assert hub.mission.facts is not None
    hub.mission.facts = replace(hub.mission.facts, active_environment_id=environment_id)
    with hub.db.begin(actor=Actor("engine", "environment"), correlation_id=hub.ids.new("cor")) as uow:
        if activated_at:
            hub.engine.apply_input(uow, EnvironmentInput(environment_id, hub.clock.now()))
        else:
            hub.engine.environment_activated(uow)


@pytest.mark.trace("T-E25-026", "V66", "D18", "UC12")
def test_environment_activation_preserves_warning_override_until_its_clear(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(8))
    hub.raise_warning()
    hub.deliver()
    before = hub.rows("SELECT override_id,revision,state FROM routine_override ORDER BY override_id")
    done = str(
        hub.rows(
            "SELECT occurrence_id FROM routine_occurrence WHERE person_id='person_mara' AND item_id='hydration'"
        )[0][0]
    )
    from crew_health.routine.application.ports import OccurrenceActionBody

    hub.commands.act(
        session(), done, OccurrenceActionBody("complete"), if_match=1, idempotency_key="done_before_move"
    )
    old = row_for(hub, "blood_pressure")
    environment(hub, "lunar_surface")
    assert hub.mission.facts is not None
    assert hub.mission.facts.active_environment_id == "lunar_surface"
    assert hub.rows("SELECT override_id,revision,state FROM routine_override ORDER BY override_id") == before
    assert hub.queries.occurrence(session(), done)["state"] == "done"
    assert hub.queries.occurrence(session(), str(old["occurrence_id"]))["environment_id"] == "station_leo"
    hub.clear_warning(reason="environment_changed")
    hub.deliver()
    assert hub.rows(
        "SELECT DISTINCT state,end_reason FROM routine_override WHERE ends_kind='warning_cleared'"
    ) == (("ended", "environment_changed"),)
    hub.advance(at(8, day=2))
    drain_timers(hub)
    assert hub.queries.task(session(), "exercise_log")["base_cadence"] == {
        "word": "daily",
        "n": None,
        "note": "surface walks add load; log them as activity",
    }
    assert all(
        r[0] == "lunar_surface"
        for r in hub.rows("SELECT environment_id FROM routine_occurrence WHERE mission_day=2")
    )


@pytest.mark.trace("T-E25-033", "UC05", "V58")
def test_storm_adds_dosimeter_at_alert_each_hour_and_clear(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    environment(hub, "transit")
    from crew_health.routine.application.ports import OccurrenceActionBody

    periodic = str(
        hub.rows(
            "SELECT occurrence_id FROM routine_occurrence WHERE person_id='person_mara' AND item_id='radiation_dosimeter'"
        )[0][0]
    )
    hub.commands.act(
        session(),
        periodic,
        OccurrenceActionBody("complete"),
        if_match=1,
        idempotency_key="dosimeter_base_done",
    )
    hub.raise_warning("wrn_storm", "radiation_storm_shelter")
    hub.deliver()
    ref = hub.rows("SELECT override_id FROM routine_override WHERE item_id='radiation_dosimeter'")[0][0]
    assert hub.rows(
        "SELECT due_from FROM routine_occurrence WHERE override_id=? AND person_id='person_mara'", (ref,)
    ) == (("2026-11-01T09:00:00Z",),)
    for hour in (10, 11):
        hub.advance(at(hour))
        drain_timers(hub)
    hub.advance(at(11, 15))
    hub.clear_warning("wrn_storm")
    hub.deliver()
    assert hub.rows(
        "SELECT due_from FROM routine_occurrence WHERE override_id=? AND person_id='person_mara' ORDER BY due_from",
        (ref,),
    ) == (
        ("2026-11-01T09:00:00Z",),
        ("2026-11-01T10:00:00Z",),
        ("2026-11-01T11:00:00Z",),
        ("2026-11-01T11:15:00Z",),
    )
    assert hub.rows("SELECT state,end_reason FROM routine_override WHERE override_id=?", (ref,)) == (
        ("ended", "warning_cleared"),
    )


@pytest.mark.trace("T-E25-034", "D17")
def test_five_point_two_hour_sleep_adds_rule_reaction_at_seven_thirty(tmp_path: Path) -> None:
    hub = station(tmp_path)
    hub.advance(at(0, day=2))
    drain_timers(hub)
    record = observation(hub, value="5.2")
    with hub.db.begin(actor=Actor("person", "person_mara"), correlation_id="sleep_52") as uow:
        hub.records.add_observation(uow, record)
        hub.runtime.evaluate(uow, evaluation(record))
    assert hub.rows(
        "SELECT due_from,started_by FROM routine_occurrence WHERE item_id='reaction_test' AND periodic=0"
    ) == (("2026-11-02T07:30:00Z", "rule"),)
    wrong = observation(hub, identifier="obs_wrong_unit", value="5.2", unit="min")
    with hub.db.begin(actor=Actor("person", "person_mara"), correlation_id="sleep_wrong_unit") as uow:
        hub.records.add_observation(uow, wrong)
        hub.runtime.evaluate(uow, evaluation(wrong))
    assert audited(hub, "binding_unit_mismatch")
    assert hub.rows(
        "SELECT count(*) FROM routine_occurrence WHERE item_id='reaction_test' AND periodic=0"
    ) == ((1,),)


@pytest.mark.trace("T-E25-046", "INV-08", "R-E25-20")
def test_environment_cadence_event_is_idempotent_per_activation(tmp_path: Path) -> None:
    hub = station(tmp_path)
    install(
        hub,
        changed_rules(
            hub,
            "rel_environment",
            "gravity_arrival_check.landing",
            on={"kind": "environment_activated", "environments": ["station_leo"]},
        ),
    )
    environment(hub, "station_leo")
    before = hub.rows("SELECT activation_id,revision FROM routine_cadence_activation ORDER BY activation_id")
    assert len(before) == 4
    environment(hub, "station_leo")
    assert (
        hub.rows("SELECT activation_id,revision FROM routine_cadence_activation ORDER BY activation_id")
        == before
    )
    hub.advance(at(1, day=2))
    environment(hub, "station_leo")
    assert hub.rows("SELECT count(*) FROM routine_cadence_activation") == ((8,),)


@pytest.mark.trace("T-E25-048", "CONTRACTS 8")
def test_mid_mission_joiner_gets_today_without_inheriting_earlier_overrides(tmp_path: Path) -> None:
    hub = station(tmp_path)
    hub.raise_warning()
    hub.deliver()
    old = hub.rows("SELECT override_id,member_ids_json FROM routine_override ORDER BY override_id")
    hub.advance(at(8, day=5))
    hub.roster.roster = (*hub.roster.roster, RosterMember("person_new", "New member", 4, None))
    hub.engine.startup_sweep()
    assert hub.rows("SELECT DISTINCT mission_day FROM routine_occurrence WHERE person_id='person_new'") == (
        (5,),
    )
    assert hub.rows("SELECT override_id,member_ids_json FROM routine_override ORDER BY override_id") == old
    assert hub.queries.overrides(session("person_new"), None, 100)["items"] == []
    response = hub.queries.occurrences(session("person_new"), "today", None, None, 100)
    assert all(r["person_id"] == "person_new" for r in cast(list[dict[str, object]], response["items"]))


@pytest.mark.trace("T-E25-050", "R-E25-22")
def test_pressure_trigger_zero_adds_one_as_needed_occurrence(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    # CONTRACTS 15: trigger 0 is manual; the landing binding owns trigger 2.
    body = {
        "item_id": "blood_pressure",
        "trigger_index": 0,
        "person_id": "person_mara",
        "until": {"kind": "person_stops"},
    }
    # Finalize the periodic slot so coalescing cannot absorb the trigger.
    row = hub.occurrence(item="blood_pressure")
    assert (
        hub.client.post(
            f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
            json={"action": "complete"},
            headers=headers(key="bp_periodic_done", revision=1),
        ).status_code
        == 200
    )
    response = hub.client.post(
        "/v1/routine/overrides",
        json=body,
        headers=headers("person_tomas", "crew_medical_officer", key="pressure_symptoms"),
    )
    assert response.status_code == 201, response.text
    assert hub.rows(
        "SELECT count(*) FROM routine_occurrence WHERE item_id='blood_pressure' AND periodic=0"
    ) == ((1,),)
    task = hub.client.get("/v1/routine/tasks/blood_pressure", headers=headers()).json()
    assert task["cadence_text"] == "When needed, measure now, then daily for 3 days"
    from crew_health.contracts_gen.enums import ConfigName

    from .support import example

    words = example("ui-copy")
    cast(dict[str, object], words["cadence_words"])["as_needed"] = "as needed"
    activate(hub, edited_release(hub, "rel_as_needed", {ConfigName.UI_COPY: words}))
    assert (
        hub.client.get("/v1/routine/tasks/blood_pressure", headers=headers()).json()["cadence_text"]
        == "As needed, measure now, then daily for 3 days"
    )


@pytest.mark.trace("T-E25-095", "INV-15")
def test_off_roster_and_live_origin_actions_are_denied_and_audited(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    path = f"/v1/routine/occurrences/{row['occurrence_id']}/actions"
    hub.roster.roster = tuple(m for m in hub.roster.roster if m.person_id != "person_mara")
    response = hub.client.post(
        path, json={"action": "complete"}, headers=headers(key="off_roster_action", revision=1)
    )
    problem(response, "UNAUTHORIZED_SCOPE", 403)
    assert audited(hub, "unauthorized")
    assert hub.rows("SELECT count(*) FROM platform_command_ledger") == ((0,),)
    hub.roster.roster = (*hub.roster.roster, RosterMember("person_mara", "Mara", 0, 0))
    assert hub.mission.facts is not None
    hub.mission.facts = replace(hub.mission.facts, mission_mode="LIVE")
    response = hub.client.post(
        path, json={"action": "complete"}, headers=headers(key="sim_on_live", revision=1)
    )
    problem(response, "UNAUTHORIZED_SCOPE", 403)
    assert hub.rows(
        "SELECT state FROM routine_occurrence WHERE occurrence_id=?", (str(row["occurrence_id"]),)
    ) == (("due",),)


@pytest.mark.trace("T-E25-095", "INV-15")
def test_occurrence_from_previous_mission_is_denied_and_audited(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    assert hub.mission.facts is not None
    hub.mission.facts = replace(hub.mission.facts, mission_id="mission_other")
    response = hub.client.post(
        f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
        json={"action": "complete"},
        headers=headers(key="old_mission_action", revision=1),
    )
    problem(response, "UNAUTHORIZED_SCOPE", 403)
    assert audited(hub, "unauthorized")
    assert hub.rows("SELECT count(*) FROM platform_command_ledger") == ((0,),)


@pytest.mark.trace("T-E25-095", "INV-15")
def test_foreign_mission_link_is_rejected_and_audited_without_a_command(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    record = replace(observation(hub), mission_id="mission_foreign")
    with hub.db.begin(actor=Actor("person", "person_mara"), correlation_id="foreign_mission_record") as uow:
        hub.records.add_observation(uow, record)
    row = hub.occurrence()
    response = hub.client.post(
        f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
        json={"action": "complete", "observation_ids": [record.observation_id]},
        headers=headers(key="foreign_mission_action", revision=1),
    )
    problem(response, "UNAUTHORIZED_SCOPE", 403)
    assert audited(hub, "unauthorized")
    assert hub.rows("SELECT count(*) FROM platform_command_ledger") == ((0,),)


@pytest.mark.trace("T-E25-097", "4.4", "SYSTEM 8.6")
def test_storage_failure_rolls_back_http_action_and_same_key_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    path = f"/v1/routine/occurrences/{row['occurrence_id']}/actions"
    auth = headers(key="storage_retry", revision=1)
    before = hub.rows("SELECT count(*) FROM routine_occurrence_transition")

    def fail(self: SqliteRoutineStore, uow: UnitOfWork, *args: object, **kwargs: object) -> None:
        raise StorageUnavailable("disk_full")

    with monkeypatch.context() as patch:
        patch.setattr(SqliteRoutineStore, "append_transition", fail)
        response = hub.client.post(path, json={"action": "complete"}, headers=auth)
    assert response.status_code == 500, response.text
    assert response.json()["retryable"] is True
    assert any(component.code == "disk_full" for component in hub.health.snapshot().components)
    assert hub.rows("SELECT count(*) FROM routine_occurrence_transition") == before
    assert hub.rows("SELECT count(*) FROM platform_command_ledger WHERE command_key='storage_retry'") == (
        (0,),
    )
    assert hub.client.post(path, json={"action": "complete"}, headers=auth).json()["state"] == "done"


@pytest.mark.trace("T-E25-099", "3.11")
def test_barrier_answer_and_abandonment_have_distinct_outcomes(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(8))
    for person in ("person_mara", "person_tomas"):
        with hub.db.begin(actor=Actor("engine", "routine"), correlation_id=hub.ids.new("cor")) as uow:
            hub.engine.deps.store.put_barrier_ask(
                uow, person, 1, ("blood_pressure",), "pending", None, uow.now
            )
            uow.publish(DailyCheckAbandoned("dc_abandoned", person, 1))
    assert hub.rows("SELECT DISTINCT outcome FROM routine_barrier_ask") == (("pending",),)
    hub.answer("barrier_reason", answer_id="ans_barrier")
    assert hub.rows("SELECT outcome FROM routine_barrier_ask WHERE person_id='person_mara'") == (
        ("answered",),
    )
    with hub.db.begin(actor=Actor("engine", "dailycheck"), correlation_id="check_unanswered") as uow:
        uow.publish(DailyCheckSubmitted("dc_submitted", "person_tomas", 1, "SIM", uow.now))
    assert hub.rows("SELECT outcome FROM routine_barrier_ask WHERE person_id='person_tomas'") == (
        ("shown_unanswered",),
    )


@pytest.mark.trace("T-E25-103", "3.12.3")
def test_removed_item_keeps_open_pinned_content_and_accepts_pinned_completion(tmp_path: Path) -> None:
    from crew_health.contracts_gen.enums import ConfigName

    hub = HttpHarness(tmp_path)
    original_steps = hub.client.get("/v1/routine/tasks/blood_pressure", headers=headers()).json()["steps"]
    row = hub.occurrence(item="blood_pressure")
    schedule, rules = document("routine-schedule"), document("routine-rules")
    schedule["items"] = [
        i for i in cast(list[dict[str, object]], schedule["items"]) if i["id"] != "blood_pressure"
    ]
    rules["bindings"] = [
        b
        for b in cast(list[dict[str, object]], rules["bindings"])
        if cast(dict[str, object], b["binds"])["item_id"] != "blood_pressure"
    ]
    rules["item_policies"] = [
        p for p in cast(list[dict[str, object]], rules["item_policies"]) if p["item_id"] != "blood_pressure"
    ]
    activate(
        hub,
        edited_release(
            hub, "rel_removed", {ConfigName.ROUTINE_SCHEDULE: schedule, ConfigName.ROUTINE_RULES: rules}
        ),
    )
    path = f"/v1/routine/occurrences/{row['occurrence_id']}"
    assert hub.client.get(path, headers=headers()).json() == row
    pinned = hub.client.get("/v1/routine/tasks/blood_pressure", headers=headers())
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["steps"] == original_steps
    response = hub.client.post(
        path + "/actions",
        json={"action": "complete"},
        headers=headers(key="removed_complete", revision=1, release=str(row["config_release_id"])),
    )
    assert response.status_code == 200, response.text
    assert response.json()["config_release_id"] == row["config_release_id"]
    assert hub.rows(
        "SELECT guidance_release_id FROM routine_occurrence WHERE occurrence_id=?",
        (str(row["occurrence_id"]),),
    ) == ((row["config_release_id"],),)


@pytest.mark.trace("T-E25-107", "CONTRACTS 5")
def test_concurrent_actions_with_same_version_have_one_winner(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    path = f"/v1/routine/occurrences/{row['occurrence_id']}/actions"
    barrier = Barrier(2)

    def submit(index: int) -> tuple[int, dict[str, object]]:
        barrier.wait(timeout=10)
        response = hub.client.post(
            path, json={"action": "complete"}, headers=headers(key=f"concurrent_{index}", revision=1)
        )
        return response.status_code, cast(dict[str, object], response.json())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(submit, (1, 2)))
    assert sorted(status for status, _ in results) == [200, 409]
    loser = next(body for status, body in results if status == 409)
    assert loser["code"] == "STALE_VERSION"
    assert hub.rows(
        "SELECT count(*) FROM routine_occurrence_transition WHERE occurrence_id=? AND to_state='done'",
        (str(row["occurrence_id"]),),
    ) == ((1,),)


@pytest.mark.trace("T-E25-110", "3.14")
def test_revoked_care_grant_hides_occurrence_again(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    ref = str(row["occurrence_id"])
    grant = Grants(frozenset({ref}))
    hub.runtime.assemble(replace(hub.engine.deps, visibility=grant))
    auth = headers("person_tomas", "crew_medical_officer")
    assert hub.client.get(f"/v1/routine/occurrences/{ref}", headers=auth).status_code == 200
    assert audited(hub, "care_read")
    grant.refs = frozenset()
    problem(hub.client.get(f"/v1/routine/occurrences/{ref}", headers=auth), "NOT_FOUND", 404)


@pytest.mark.trace("T-E25-111", "3.12.1", "INV-12")
def test_warning_start_and_end_change_task_cadence_and_provenance_in_one_release(tmp_path: Path) -> None:
    from crew_health.contracts_gen.enums import ConfigName

    hub = HttpHarness(tmp_path)
    schedule = document("routine-schedule")
    item = next(i for i in cast(list[dict[str, object]], schedule["items"]) if i["id"] == "vision_self_check")
    cast(dict[str, object], item["cadence"])["basis"] = "sourced"
    activate(hub, edited_release(hub, "rel_provenance", {ConfigName.ROUTINE_SCHEDULE: schedule}))
    before = hub.queries.task(session(), "vision_self_check")
    assert before["basis"] == "sourced"
    hub.raise_warning()
    hub.deliver()
    changed = hub.queries.task(session(), "vision_self_check")
    assert changed["cadence_text"] == "Daily, while CO2 stays above the limit"
    assert changed["basis"] == "team default"
    assert cast(dict[str, object], changed["citation"])["source_ids"] == ["C25"]
    hub.clear_warning()
    hub.deliver()
    after = hub.queries.task(session(), "vision_self_check")
    for key in ("cadence_text", "basis", "citation"):
        assert after[key] == before[key]
    assert hub.release.release_id == "rel_provenance"


@pytest.mark.trace("T-E25-112", "R-E25-23", "D34")
def test_conference_unavailable_without_ground_still_gets_next_periodic_reminder(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    task = next(t for t in hub.items("/v1/routine/tasks") if "conference" in str(t["item_id"]))
    row = hub.occurrence(item=str(task["item_id"]))
    response = hub.client.post(
        f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
        json={"action": "skip", "skip_reason": "not_available"},
        headers=headers(key="conference_no_link", revision=1),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "skipped"
    hub.advance(at(8) + timedelta(days=60))
    hub.engine.startup_sweep()
    assert hub.rows(
        "SELECT state FROM routine_occurrence WHERE occurrence_id=?", (str(row["occurrence_id"]),)
    ) == (("skipped",),)
    assert hub.rows(
        "SELECT mission_day,state FROM routine_occurrence WHERE person_id='person_mara' AND item_id=? ORDER BY mission_day",
        (str(task["item_id"]),),
    )[-1] == (61, "due")

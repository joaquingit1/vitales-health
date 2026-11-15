"""Input edges: audiences, re-raised warnings, lost inserts, effect completion bounds, timers and answers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import Actor
from crew_health.routine.application.ports import OccurrenceActionBody

from .harness import RoutineHarness, session, station
from .test_bounds import changed_rules, install
from .test_branch_edges_activations import lose
from .test_integration_inputs import audited

ENGINE = Actor("engine", "engine_routine")


def at(hour: int, minute: int = 0, *, day: int = 1) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def overrides(hub: RoutineHarness) -> tuple[tuple[object, ...], ...]:
    return hub.rows(
        "SELECT item_id,state,source_ref,revision,end_reason FROM routine_override ORDER BY item_id,override_id"
    )


def override_id(hub: RoutineHarness, item: str) -> str:
    return str(hub.rows("SELECT override_id FROM routine_override WHERE item_id=?", (item,))[0][0])


def job(key: str, handler: str, payload: dict[str, str], due: datetime) -> ScheduledJob:
    return ScheduledJob(key, handler, AbsoluteDeadline(due), payload, None)


def generation(hub: RoutineHarness, key: str) -> int | None:
    with hub.db.snapshot() as view:
        current: int | None = hub.engine.deps.store.deadline_generation(view, key)
    return current


def test_a_person_binding_on_a_warning_without_a_subject_reaches_nobody(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    install(hub, changed_rules(hub, "rel_person", "vision_self_check.co2_above_limit", audience="person"))
    hub.raise_warning()
    hub.deliver()
    assert [row[0] for row in overrides(hub)] == ["daily_check"]
    assert audited(hub, "roster_unavailable")


def test_a_re_raise_naming_an_unknown_predecessor_starts_fresh_overrides(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning("wrn_co2_2", previous_warning_id="wrn_co2_unknown")
    hub.deliver()
    assert [(row[0], row[1], row[2], row[3]) for row in overrides(hub)] == [
        ("daily_check", "active", "wrn_co2_2", 1),
        ("vision_self_check", "active", "wrn_co2_2", 1),
    ]


def test_a_re_raise_repoints_each_binding_to_its_own_override(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    hub.raise_warning("wrn_co2_2", previous_warning_id="wrn_co2_1")
    hub.deliver()
    assert [(row[0], row[1], row[2], row[3]) for row in overrides(hub)] == [
        ("daily_check", "active", "wrn_co2_2", 2),
        ("vision_self_check", "active", "wrn_co2_2", 2),
    ]


def test_overrides_and_question_instances_lost_to_concurrent_inserts_add_nothing(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    original = hub.engine.deps.store
    lose(hub, "insert_override")
    hub.raise_warning()
    hub.deliver()
    assert overrides(hub) == ()
    hub.restart(replace(hub.engine.deps, store=original))
    lose(hub, "insert_instance")
    hub.raise_warning("wrn_co2_2", definition_id="co2_above_limit")
    hub.deliver()
    assert len(overrides(hub)) == 2
    assert hub.rows("SELECT COUNT(*) FROM routine_question_instance") == ((0,),)
    assert hub.rows("SELECT COUNT(*) FROM platform_schedule WHERE key LIKE 'routine.qi.%'") == ((0,),)


def dust_targets(hub: RoutineHarness) -> list[tuple[str, str, int]]:
    rows = hub.rows(
        "SELECT o.person_id,o.occurrence_id,o.revision FROM routine_override_added a "
        "JOIN routine_occurrence o ON o.occurrence_id=a.target_id "
        "WHERE o.item_id='dust_exposure_check' ORDER BY o.person_id"
    )
    return [(str(p), str(i), int(str(r))) for p, i, r in rows]


def complete(hub: RoutineHarness, person: str, identifier: str, revision: int) -> None:
    status, _ = hub.commands.act(
        session(person),
        identifier,
        OccurrenceActionBody("complete"),
        if_match=revision,
        idempotency_key="cmd_" + identifier,
    )
    assert status == 200


def test_effect_completion_waits_for_every_target_and_stops_at_its_scan_bound(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning("wrn_dust_1", "dust_lunar_act")
    hub.deliver()
    targets = dust_targets(hub)
    assert len(targets) == 3
    complete(hub, *targets[0])
    assert overrides(hub)[0][:2] == ("dust_exposure_check", "active")
    deps = hub.engine.deps
    hub.restart(
        replace(
            deps,
            settings=replace(
                deps.settings, page_limit=1, max_roster=1, max_slots_per_binding=1, history_days=1
            ),
        )
    )
    for target in targets[1:]:
        complete(hub, *target)
    # Two full pages fill the bound of 2 before the third target is read: no decision, capacity reported.
    assert overrides(hub)[0][:2] == ("dust_exposure_check", "active")
    assert audited(hub, "capacity")
    assert any(c.code == "ROUTINE_CAPACITY" for c in hub.health.snapshot().components)


def test_a_mission_event_from_a_rejected_origin_ends_and_starts_nothing(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9), mode="LIVE")
    hub.mission_event(origin_mode="SIM")
    hub.deliver()
    assert overrides(hub) == ()
    assert hub.rows("SELECT COUNT(*) FROM routine_cadence_activation") == ((0,),)
    assert audited(hub, "origin_rejected")


def test_an_override_ends_only_on_the_mission_event_its_binding_names(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    install(
        hub,
        changed_rules(
            hub,
            "rel_until_launch",
            "blood_pressure.return_to_gravity",
            until={"kind": "mission_event", "event_types": ["launch"]},
        ),
    )
    hub.mission_event()
    hub.deliver()
    assert overrides(hub)[0][:2] == ("blood_pressure", "active")
    hub.mission_event("space_weather_alert", event_id="evt_weather")
    hub.deliver()
    assert ("blood_pressure", "active") in {row[:2] for row in overrides(hub)}
    hub.mission_event("launch", event_id="evt_launch")
    hub.deliver()
    assert next(row for row in overrides(hub) if row[0] == "blood_pressure")[1::3] == (
        "ended",
        "mission_event",
    )


def test_override_timers_change_nothing_early_or_for_unknown_and_endless_overrides(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.mission_event("space_weather_alert", event_id="evt_weather")
    hub.deliver()
    before = overrides(hub)
    vision = override_id(hub, "vision_self_check")
    weather = str(
        hub.rows("SELECT override_id FROM routine_override WHERE ends_kind='days' AND item_id='daily_check'")[
            0
        ][0]
    )
    key = "routine.ovr." + weather
    first = generation(hub, key)
    now = hub.clock.now()
    with hub.db.begin(actor=ENGINE, correlation_id="cor_override_jobs") as uow:
        inputs = hub.engine.inputs
        inputs.override_job(
            uow, job(key, "routine.override", {"override_id": "rov_missing", "kind": "ends"}, now)
        )
        inputs.override_job(uow, job(key, "routine.override", {"override_id": vision, "kind": "ends"}, now))
        every = {"override_id": vision, "kind": "every", "slot": "1"}
        inputs.override_job(uow, job("routine.every." + vision, "routine.override", every, now))
        inputs.override_job(uow, job(key, "routine.override", {"override_id": weather, "kind": "ends"}, now))
    assert overrides(hub) == before
    assert generation(hub, "routine.every." + vision) is None
    later = generation(hub, key)
    assert first is not None and later is not None and later > first


def repeat_instance(hub: RoutineHarness) -> tuple[str, str, str]:
    row = hub.rows(
        "SELECT q.instance_id,q.due_until,o.occurrence_id FROM routine_question_instance q "
        "JOIN routine_occurrence o ON q.carrier_occurrence_id=o.occurrence_id "
        "WHERE q.person_id='person_mara' AND q.slot='repeat:0'"
    )[0]
    return str(row[0]), str(row[1]), str(row[2])


def test_instance_timers_wait_for_their_end_and_an_expired_instance_misses_its_carrier_uncounted(
    tmp_path: Path,
) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    instance, until, carrier = repeat_instance(hub)
    key = "routine.qi." + instance
    first = generation(hub, key)
    with hub.db.begin(actor=ENGINE, correlation_id="cor_instance_early") as uow:
        hub.engine.inputs.instance_job(
            uow, job(key, "routine.instance", {"instance_id": "rq_missing"}, uow.now)
        )
        hub.engine.inputs.instance_job(uow, job(key, "routine.instance", {"instance_id": instance}, uow.now))
    later = generation(hub, key)
    assert first is not None and later is not None and later > first
    assert hub.rows("SELECT state FROM routine_question_instance WHERE instance_id=?", (instance,)) == (
        ("open",),
    )
    hub.advance(datetime.fromisoformat(until))
    with hub.db.begin(actor=ENGINE, correlation_id="cor_instance_due") as uow:
        hub.engine.inputs.instance_job(uow, job(key, "routine.instance", {"instance_id": instance}, uow.now))
    assert hub.rows("SELECT state FROM routine_question_instance WHERE instance_id=?", (instance,)) == (
        ("unanswered",),
    )
    assert hub.rows(
        "SELECT state,miss_counted FROM routine_occurrence WHERE occurrence_id=?", (carrier,)
    ) == (("missed", 0),)


def test_answers_from_another_mission_or_off_the_roster_are_refused(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    with hub.db.snapshot() as view:
        question = hub.engine.release(view).misses.question_id
    hub.answer(question, person_id="person_stranger", answer_id="answer_stranger")
    assert audited(hub, "origin_rejected")
    hub.answer(question, answer_id="answer_own")
    assert not audited(hub, "foreign_mission")
    with hub.db.begin(actor=ENGINE, correlation_id="cor_foreign") as uow:
        answer = hub.records.answer(uow, "answer_own")
        assert answer is not None
        hub.engine.on_answer(uow, answer, "mission_other")
    assert audited(hub, "foreign_mission")


def test_each_day_questions_skip_an_override_whose_pin_cannot_be_read(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.mission_event("space_weather_alert", event_id="evt_weather")
    hub.deliver()
    weather = override_id(hub, "daily_check")
    with hub.db.begin(actor=ENGINE, correlation_id="cor_evict") as uow:
        stored = hub.engine.deps.store.get_override(uow, weather)
        assert stored is not None
        hub.engine.deps.store.update_override(
            uow,
            replace(stored, revision=stored.revision + 1, config_release_id="rel_evicted"),
            expected_revision=stored.revision,
        )
    before = hub.rows("SELECT COUNT(*) FROM routine_question_instance")
    hub.advance(at(9, day=2))
    with hub.db.begin(actor=ENGINE, correlation_id="cor_each_day") as uow:
        hub.engine.inputs.each_day(uow, "person_mara", 2)
    assert hub.rows("SELECT COUNT(*) FROM routine_question_instance") == before
    assert any(c.code == "CONFIG_INVALID" for c in hub.health.snapshot().components)

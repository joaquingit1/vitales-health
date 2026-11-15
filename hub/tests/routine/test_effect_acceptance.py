"""Configured effects keep their temporal, audience and completion contracts."""

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor
from crew_health.routine.application.ports import OccurrenceActionBody

from .harness import RoutineHarness, instant, session, station
from .test_bounds import changed_rules, install
from .test_integration_inputs import at, audited, drain_timers


def paired_cadence(hub: RoutineHarness, cadence: dict[str, object]) -> MemoryRelease:
    release = changed_rules(
        hub,
        "rel_cadence_effect",
        "vision_self_check.co2_above_limit",
        effect={"kind": "replace_cadence", "cadence": cadence},
    )
    documents = dict(release.documents)
    schedule = cast(dict[str, object], json.loads(documents[ConfigName.ROUTINE_SCHEDULE].raw))
    item = next(
        item for item in cast(list[dict[str, object]], schedule["items"]) if item["id"] == "vision_self_check"
    )
    triggers = cast(list[dict[str, object]], item["raise_frequency_when"])
    triggers[1]["new_cadence"] = cadence
    raw = canonical_json(schedule)
    documents[ConfigName.ROUTINE_SCHEDULE] = ConfigDocumentView(
        ConfigName.ROUTINE_SCHEDULE, sha256(raw).hexdigest(), raw
    )
    return MemoryRelease(release.release_id, documents)


@pytest.mark.trace("T-E25-043", "T-E25-015")
def test_times_slot_uses_source_day_offset_and_remains_one_future_occurrence(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    release = changed_rules(
        hub,
        "rel_times",
        "vision_self_check.co2_above_limit",
        effect={
            "kind": "add_occurrences",
            "at_trigger": False,
            "times": [{"day_offset": 1, "local_time": "10:30"}],
            "every_minutes": None,
            "at_clear": False,
            "basis": "team default",
        },
    )
    install(hub, release)
    for _ in range(2):
        hub.raise_warning()
        hub.deliver()
    rows = hub.rows(
        "SELECT mission_day,due_from,due_until,state,cause_key FROM routine_occurrence WHERE person_id=? AND cause_kind='override' AND item_id='vision_self_check'",
        ("person_mara",),
    )
    identifier = str(
        hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0]
    )
    assert rows == (
        (2, "2026-11-02T10:30:00Z", "2026-11-02T12:30:00Z", "not_due", f"override:{identifier}:t0"),
    )


@pytest.mark.trace("T-E25-007", "T-E25-043")
@pytest.mark.parametrize("word", ("monthly", "after_event"))
def test_looser_or_event_cadence_adds_at_trigger_without_moving_periodic_slot(
    tmp_path: Path, word: str
) -> None:
    hub = station(tmp_path, at=at(9))
    cadence: dict[str, object] = {"word": word, "n": None, "note": "Configured temporary effect"}
    if word == "after_event":
        cadence["event"] = "exercise_ended"
    install(hub, paired_cadence(hub, cadence))
    before = hub.rows(
        "SELECT occurrence_id,mission_day,due_from FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check' AND periodic=1",
        ("person_mara",),
    )
    hub.raise_warning()
    hub.deliver()
    assert (
        hub.rows(
            "SELECT occurrence_id,mission_day,due_from FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check' AND periodic=1",
            ("person_mara",),
        )
        == before
    )
    assert hub.rows(
        "SELECT periodic,due_from,due_until FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check' AND cause_kind='override'",
        ("person_mara",),
    ) == ((0, "2026-11-01T09:00:00Z", "2026-11-01T11:00:00Z"),)
    task = hub.queries.task(session(), "vision_self_check")
    assert task["effective_cadence"] == task["base_cadence"]


@pytest.mark.trace("T-E25-043", "D16", "T-E25-015")
def test_each_occurrence_questions_renew_once_on_next_mission_day(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    release = changed_rules(
        hub,
        "rel_each_day",
        "daily_check.co2_above_limit",
        audience="person",
        effect={
            "kind": "add_questions",
            "question_set_id": "co2_headache_vision",
            "attach_to": "each_occurrence",
            "repeat_after_minutes": [],
            "basis": "team default",
        },
        until={"kind": "days", "days": 3, "basis": "team default"},
    )
    install(hub, release)
    hub.raise_warning(scope="person", person_id="person_mara")
    hub.deliver()
    with hub.db.begin(actor=Actor("engine", "routine_day"), correlation_id="cor_materialize_same") as uow:
        hub.engine.materialize(uow, "person_mara", 1)
    assert hub.rows(
        "SELECT slot,carrier_day FROM routine_question_instance WHERE person_id=?", ("person_mara",)
    ) == (("initial", 1),)
    hub.advance(at(0, day=2))
    drain_timers(hub)
    with hub.db.begin(actor=Actor("engine", "routine_day"), correlation_id="cor_materialize_repeat") as uow:
        hub.engine.materialize(uow, "person_mara", 2)
    rows = hub.rows(
        "SELECT slot,carrier_day,state FROM routine_question_instance WHERE person_id=? ORDER BY due_from",
        ("person_mara",),
    )
    assert rows == (("initial", 1, "unanswered"), ("day:2:initial", 2, "open"))
    assert hub.runtime.added_questions("person_mara", 2, hub.clock.now())


@pytest.mark.trace("T-E25-043", "T-E25-042", "D16")
def test_all_verified_answers_complete_carriers_and_end_effect_once_all_targets_final(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    daily = hub.rows(
        "SELECT occurrence_id,revision FROM routine_occurrence WHERE person_id=? AND item_id='daily_check' AND periodic=1",
        ("person_mara",),
    )[0]
    hub.commands.act(
        session(),
        str(daily[0]),
        OccurrenceActionBody("complete"),
        if_match=int(str(daily[1])),
        idempotency_key="key_daily_complete",
        config_release=None,
    )
    install(hub, changed_rules(hub, "rel_answer_all", "daily_check.co2_above_limit", audience="person"))
    hub.raise_warning(scope="person", person_id="person_mara")
    hub.deliver()
    identifier = str(
        hub.rows("SELECT override_id FROM routine_override WHERE binding_id='daily_check.co2_above_limit'")[
            0
        ][0]
    )
    with hub.db.snapshot() as view:
        instances = hub.engine.deps.store.open_instances_for_person(view, "person_mara", 100)
    assert len(instances) == 2
    for index, question in enumerate(instances[0].question_ids):
        hub.answer(question, answer_id=f"answer_initial_{index}")
    assert hub.rows(
        "SELECT state FROM routine_question_instance WHERE instance_id=?", (instances[0].instance_id,)
    ) == (("answered",),)
    assert hub.rows("SELECT state FROM routine_override WHERE override_id=?", (identifier,)) == (("active",),)
    hub.advance(at(11))
    for index, question in enumerate(instances[1].question_ids):
        hub.answer(question, answer_id=f"answer_repeat_{index}")
    assert hub.rows(
        "SELECT state FROM routine_question_instance WHERE override_id=? ORDER BY due_from", (identifier,)
    ) == (("answered",), ("answered",))
    assert hub.rows(
        "SELECT state FROM routine_occurrence WHERE override_id=? ORDER BY due_from", (identifier,)
    ) == (("done",), ("done",))
    assert hub.rows(
        "SELECT state,end_reason,revision FROM routine_override WHERE override_id=?", (identifier,)
    ) == (("ended", "effect_complete", 2),)
    hub.answer(instances[1].question_ids[-1], answer_id="answer_repeat_duplicate")
    assert hub.rows("SELECT revision FROM routine_override WHERE override_id=?", (identifier,)) == ((2,),)


@pytest.mark.trace("T-E25-043", "INV-E25-16")
def test_missing_optional_question_catalog_audits_unknown_set_without_instances(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    documents = {
        name: hub.release.document(name)
        for name in (ConfigName.ROUTINE_SCHEDULE, ConfigName.ROUTINE_RULES, ConfigName.UI_COPY)
    }
    install(hub, MemoryRelease("rel_no_question_catalog", documents))
    hub.raise_warning()
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_question_instance") == ((0,),)
    assert audited(hub, "question_set_unknown")
    assert hub.rows(
        "SELECT COUNT(*) FROM routine_override WHERE binding_id='daily_check.co2_above_limit'"
    ) == ((1,),)


@pytest.mark.trace("T-E25-007", "D16", "V54")
def test_absent_location_provider_uses_site_members_except_active_walkers(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.overlay("ovl_active_walk", person_id="person_ilya", kind="eva", started_at=at(8))
    hub.deliver()
    hub.engine.deps = replace(hub.engine.deps, locations=None)
    hub.engine.inputs.deps = hub.engine.deps
    hub.raise_warning()
    hub.deliver()
    identifier = hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0]
    assert hub.rows(
        "SELECT person_id,member_basis FROM routine_override_member WHERE override_id=? ORDER BY person_id",
        (identifier,),
    ) == (("person_mara", "site"), ("person_sofia", "site"), ("person_tomas", "site"))


@pytest.mark.trace("T-E25-029", "INV-E25-16")
def test_roster_larger_than_operational_bound_rejects_trigger(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9), values={"routine.max_roster": 3})
    hub.raise_warning()
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "roster_unavailable")


@pytest.mark.trace("T-E25-029", "V72")
def test_input_subject_not_on_roster_is_audited_and_never_targets_other_members(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning(scope="person", person_id="person_absent")
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "not_on_roster")


@pytest.mark.trace("T-E25-007", "T-E25-100")
def test_environment_changed_warning_clear_records_specific_end_reason(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.raise_warning()
    hub.deliver()
    hub.clear_warning(reason="environment_changed")
    hub.deliver()
    assert hub.rows("SELECT state,end_reason FROM routine_override WHERE item_id='vision_self_check'") == (
        ("ended", "environment_changed"),
    )


@pytest.mark.trace("T-E25-029", "V72", "D16")
def test_simulated_mission_retains_replay_warning_provenance_on_added_occurrences(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    source_at = at(8, 55)
    hub.raise_warning(
        "wrn_replay_dust",
        "dust_lunar_act",
        origin_mode="REPLAY",
        raised_at=instant(source_at),
        evidence_time=instant(source_at),
    )
    hub.deliver()
    assert hub.rows(
        "SELECT source_origin_mode,source_at FROM routine_override WHERE item_id='dust_exposure_check'"
    ) == (("REPLAY", instant(source_at)),)
    assert hub.rows(
        "SELECT origin_mode,due_from FROM routine_occurrence WHERE item_id='dust_exposure_check' AND cause_kind='override' ORDER BY person_id"
    ) == (("REPLAY", instant(source_at)), ("REPLAY", instant(source_at)), ("REPLAY", instant(source_at)))

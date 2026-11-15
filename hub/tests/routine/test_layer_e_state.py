"""Pinned guidance, calendar anchors and bounded warning intake."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName, EventType
from crew_health.kernel.uow import Actor
from crew_health.routine.domain.bindings import WarningRaisedInput
from crew_health.routine.domain.errors import EnvironmentAnchorUnavailable
from crew_health.routine.domain.occurrence import Outcome, complete

from .harness import RoutineHarness, session, station
from .http_harness import HttpHarness, headers
from .support import REPO, document
from .test_http_faults import activate, edited_release
from .test_integration_inputs import at, drain_timers
from .test_layer_e import environment
from .test_query_acceptance import row_for


@pytest.mark.trace("T-E25-098", "INV-17", "PRD 11", "INV-E25-17", "A8", "PLAN-row-29")
def test_task_steps_come_verbatim_from_the_release_in_live_and_sim_with_no_review_gate(
    tmp_path: Path,
) -> None:
    """A8: routine never calls guidance; PLAN row 29: clinical review absence is the release notice label, never a
    runtime gate. Expected steps are read from the schedule document itself, the served content is that text."""
    hub = HttpHarness(tmp_path)
    path = "/v1/routine/tasks/blood_pressure"
    schedule = document("routine-schedule")
    item = next(i for i in cast(list[dict[str, object]], schedule["items"]) if i["id"] == "blood_pressure")
    configured = cast(list[str], item["steps"])
    sim = hub.client.get(path, headers=headers())
    assert sim.status_code == 200
    assert sim.json()["steps"] == configured
    assert not any(name.lower().startswith("x-guidance") for name in sim.headers)
    assert hub.mission.facts is not None
    hub.mission.facts = replace(hub.mission.facts, mission_mode="LIVE")
    assert hub.client.get(path, headers=headers()).json()["steps"] == configured
    hub.mission.facts = replace(hub.mission.facts, mission_mode="SIM")
    edited = [configured[0] + " Edited instruction.", *configured[1:]]
    item["steps"] = edited
    activate(hub, edited_release(hub, "rel_steps_edit", {ConfigName.ROUTINE_SCHEDULE: schedule}))
    # PLAN row 28, M5: the open occurrence keeps the steps it started with; new work gets the edited ones.
    assert hub.client.get(path, headers=headers()).json()["steps"] == configured
    open_row = row_for(hub, "blood_pressure")
    done = hub.client.post(
        f"/v1/routine/occurrences/{open_row['occurrence_id']}/actions",
        json={"action": "complete"},
        headers=headers(key="steps_edit_done", revision=int(str(open_row["revision"]))),
    )
    assert done.status_code == 200, done.text
    assert hub.client.get(path, headers=headers()).json()["steps"] == edited


def finish_day(hub: RoutineHarness, item_id: str, day: int) -> None:
    """Seed a documented historical final through the actual domain and store."""
    from .support import occurrence

    start, finish = hub.engine.bounds(day, "clock_1")
    with hub.db.begin(actor=Actor("person", "person_mara"), correlation_id=hub.ids.new("cor")) as uow:
        release, mission = hub.engine.release(uow), hub.engine.mission(uow)
        occ = replace(
            occurrence(),
            occurrence_id=hub.ids.derived("ro", "person_mara", item_id, str(day), "cadence"),
            person_id="person_mara",
            item_id=item_id,
            mission_day=day,
            due_from=start,
            base_due_until=finish,
            due_until=finish,
            created_at=start,
            config_release_id=release.release_id,
            origin_mode=mission.mission_mode,
        )
        # Historical replay is stored as a final record, never fabricated as a live reading.
        occ, transition = complete(
            occ,
            now=finish - timedelta(minutes=1),
            completed_at=None,
            outcome=Outcome((), (), None),
            actor_kind="person",
            actor_id="person_mara",
            day_start=start,
            guidance_release_id=release.release_id,
        )
        assert hub.engine.deps.store.insert(uow, occ)
        hub.engine.deps.store.append_transition(uow, transition)


@pytest.mark.trace("T-E25-047", "DE25-11")
def test_mission_and_environment_anchors_reproduce_station_periodic_example_days(tmp_path: Path) -> None:
    expected = json.loads(
        (REPO / "contracts/api/examples/routine/today-station.json").read_text(encoding="utf-8")
    )["value"]
    weekly = json.loads(
        (REPO / "contracts/api/examples/routine/week-station.json").read_text(encoding="utf-8")
    )["value"]
    for anchor in ("mission_start", "environment_start"):
        directory = tmp_path / anchor
        directory.mkdir()
        hub = HttpHarness(directory)
        if anchor == "environment_start":
            rules = document("routine-rules")
            cast(dict[str, object], rules["calendar"])["anchor"] = "environment_start"
            activate(hub, edited_release(hub, "rel_env_anchor", {ConfigName.ROUTINE_RULES: rules}))
        profile = json.loads(
            (REPO / "contracts/api/examples/environment/profile-active-station.json").read_text(
                encoding="utf-8"
            )
        )["value"]
        # A normal environment event carries its observed activation time.
        profile["activated_at"] = "2026-11-01T00:00:00Z"
        hub.emit(EventType.ENVIRONMENT_ACTIVATED, profile, at=at(0))
        hub.deliver()
        hub.advance(at(9, 10, day=19))
        for item, day in (("body_mass", 14), ("behavioral_self_check", 17), ("hearing_check", 14)):
            finish_day(hub, item, day)
        drain_timers(hub)
        # CONTRACTS 20 explicitly supplies body mass last completed on day 14.
        row = row_for(hub, "body_mass")
        golden = next(o for g in expected["groups"] for o in g["occurrences"] if o["item_id"] == "body_mass")
        assert cast(dict[str, object], row["day"])["mission_day"] == golden["day"]["mission_day"] == 21
        assert hub.queries.schedule(session(), "week")["range"] == weekly["range"]
        assert hub.queries.schedule(session(), "week")["groups"] == weekly["groups"] == []
        assert hub.rows("SELECT environment_id,mission_day FROM routine_environment_day") == (
            ("station_leo", 1),
        )
        hub.advance(at(8, day=21))
        with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="anchor_materialize") as uow:
            hub.engine.materialize(uow, "person_mara", 21)
        assert hub.rows(
            "SELECT mission_day FROM routine_occurrence WHERE person_id='person_mara' AND item_id='body_mass' AND mission_day=21"
        ) == ((21,),)


@pytest.mark.trace("T-E25-047", "DE25-11")
def test_missing_environment_anchor_never_guesses_and_history_before_anchor_is_ignored(
    tmp_path: Path,
) -> None:
    hub = HttpHarness(tmp_path)
    rules = document("routine-rules")
    cast(dict[str, object], rules["calendar"])["anchor"] = "environment_start"
    activate(hub, edited_release(hub, "rel_env_anchor", {ConfigName.ROUTINE_RULES: rules}))
    hub.advance(at(8, day=10))
    with hub.db.snapshot() as view:
        with pytest.raises(EnvironmentAnchorUnavailable):
            hub.engine.periodic(view, "person_mara", 10, hub.engine.release(view), hub.engine.mission(view))
    hub.engine.startup_sweep()
    assert hub.rows("SELECT count(*) FROM routine_occurrence WHERE mission_day=10") == ((0,),)
    assert hub.client.get("/v1/routine/today", headers=headers()).status_code == 200
    assert any(c.code == "ENVIRONMENT_ANCHOR_UNAVAILABLE" for c in hub.health.snapshot().components)
    environment(hub, "station_leo")
    assert hub.rows(
        "SELECT mission_day FROM routine_occurrence WHERE person_id='person_mara' AND item_id='blood_pressure' AND mission_day=10"
    ) == ((10,),)


def flood(hub: RoutineHarness, count: int) -> None:
    for offset in range(0, count, 50):
        with hub.db.begin(actor=Actor("engine", "warning_ingest"), correlation_id=hub.ids.new("cor")) as uow:
            for index in range(offset, min(offset + 50, count)):
                hub.engine.apply_input(
                    uow,
                    WarningRaisedInput(
                        f"wrn_flood_{index}",
                        "co2_above_limit",
                        "sensor_limit",
                        "habitat",
                        None,
                        "cabin",
                        "SIM",
                        hub.clock.now(),
                        None,
                        None,
                        "station_leo",
                    ),
                )
    assert any(c.code == "ROUTINE_CAPACITY" for c in hub.health.snapshot().components)
    assert (
        hub.rows(
            "SELECT count(*) FROM platform_audit WHERE json_extract(envelope,'$.entry.event_type')='routine.capacity'"
        )[0][0]
        != 0
    )
    with hub.db.snapshot() as view:
        for person in hub.roster.person_ids():
            assert hub.engine.deps.store.count_open_nonassistant(view, person) <= 5
            assert hub.engine.deps.store.count_active_overrides_for(view, person) <= 2
    assert hub.runtime.schedule(session(), "today")["person_id"] == "person_mara"


@pytest.mark.trace("T-E25-105", "H16")
def test_warning_flood_enforces_caps_without_blocking_reads(tmp_path: Path) -> None:
    hub = station(
        tmp_path, values={"routine.max_open_per_person": 5, "routine.max_active_overrides_per_person": 2}
    )
    flood(hub, 300)
    with hub.db.begin(actor=Actor("assistant", "assistant_test"), correlation_id="separate_cap") as uow:
        result = hub.runtime.add_recheck(
            uow, person_id="person_mara", item_id="hydration", insight_id="ins_flood", due_from=uow.now
        )
    assert result.item_id == "hydration"


@pytest.mark.slow
@pytest.mark.trace("T-E25-105", "H16")
def test_ten_thousand_distinct_warnings_keep_every_routine_bound(tmp_path: Path) -> None:
    hub = station(
        tmp_path, values={"routine.max_open_per_person": 5, "routine.max_active_overrides_per_person": 2}
    )
    flood(hub, 10000)

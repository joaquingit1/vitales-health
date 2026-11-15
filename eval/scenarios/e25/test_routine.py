"""E25 over HTTP: SIM warning cadence, unseen outage windows and a sleep hold."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

_HARNESS = Path(__file__).resolve().parents[3] / "hub" / "tests"
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

from crew_health.routine.domain.release import ReplaceCadence, WarningRaisedOn  # noqa: E402
from routine.http_harness import HttpHarness, headers, validate  # noqa: E402
from routine.support import REPO  # noqa: E402

pytestmark = pytest.mark.trace(
    "T-E25-004", "T-E25-005", "T-E25-014", "T-E25-019", "T-E25-029", "T-E25-091", "T-E25-109"
)


def example(group: str, name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((REPO / "contracts/api/examples" / group / (name + ".json")).read_text(encoding="utf-8"))[
            "value"
        ],
    )


def instant(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def test_sim_co2_changes_location_members_vision_cadence_and_clearing_restores_it(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    warning = example("warnings", "co2-station")
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
    binding = next(
        b
        for b in release.bindings
        if isinstance(b.on, WarningRaisedOn)
        and str(warning["definition_id"]) in b.on.definition_ids
        and isinstance(b.effect, ReplaceCadence)
    )
    item = release.item(binding.item_id)
    assert item is not None
    before = {}
    for member in hub.roster.roster:
        response = hub.client.get(f"/v1/routine/tasks/{item.item_id}", headers=headers(member.person_id))
        before[member.person_id] = validate(response, "routine-task.schema.json")["effective_cadence"]
    hub.raise_warning(str(warning["warning_id"]), str(warning["definition_id"]))
    hub.deliver()
    assert hub.mission.facts is not None
    for member in hub.roster.roster:
        task = validate(
            hub.client.get(f"/v1/routine/tasks/{item.item_id}", headers=headers(member.person_id)),
            "routine-task.schema.json",
        )
        location = hub.locations.locations[member.person_id]
        if location is None or location == warning["location"]:
            trigger = item.triggers[cast(int, binding.trigger_index)]
            assert cast(dict[str, object], task["effective_cadence"])["word"] == trigger.new_cadence.word
            assert task["active_override_ids"]
        else:
            assert task["effective_cadence"] == before[member.person_id]
    hub.clear_warning(str(warning["warning_id"]))
    hub.deliver()
    for member in hub.roster.roster:
        task = hub.client.get(f"/v1/routine/tasks/{item.item_id}", headers=headers(member.person_id)).json()
        assert task["effective_cadence"] == before[member.person_id]
        assert task["active_override_ids"] == []


def test_outage_marks_the_shown_window_missed_uncounted_and_does_not_invent_unseen_days(
    tmp_path: Path,
) -> None:
    hub = HttpHarness(tmp_path)
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
    policy = next(p for p in release.policies.values() if p.window_minutes is not None)
    shown = hub.occurrence(item=policy.item_id)
    initial_day = cast(dict[str, object], shown["day"])["mission_day"]
    assert isinstance(initial_day, int)
    day, version = hub.engine.day(hub.clock.now())
    after_unseen_day = hub.engine.bounds(day + 2, version)[0]
    hub.advance(after_unseen_day)
    hub.runtime.assemble(hub.engine.deps)
    hub.dispatch()
    response = hub.client.get(f"/v1/routine/occurrences/{shown['occurrence_id']}", headers=headers())
    body = validate(response, "routine-occurrence.schema.json")
    occurrence_states = json.loads(
        (REPO / "contracts/api/schemas/routine-occurrence.schema.json").read_text(encoding="utf-8")
    )["properties"]["state"]["enum"]
    assert body["state"] == occurrence_states[-1]
    assert hub.rows(
        "SELECT miss_counted FROM routine_occurrence WHERE occurrence_id=?", (str(shown["occurrence_id"]),)
    ) == ((0,),)
    assert hub.rows("SELECT count(*) FROM routine_occurrence WHERE mission_day=?", (initial_day + 1,)) == (
        (0,),
    )
    validate(hub.client.get("/v1/routine/today", headers=headers()), "routine-schedule.schema.json")


def test_sleep_holds_the_task_and_waking_extends_its_deadline(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
    policy = next(p for p in release.policies.values() if p.window_minutes is not None)
    shown = hub.occurrence(item=policy.item_id)
    start, end = instant(shown["due_from"]), instant(shown["due_until"])
    overlay = example("environment", "overlay-sleep-planned")
    hub.advance(start)
    hub.overlay(
        str(overlay["overlay_id"]),
        person_id=str(shown["person_id"]),
        kind=str(overlay["overlay"]),
        started_at=start,
    )
    hub.deliver()
    path = f"/v1/routine/occurrences/{shown['occurrence_id']}"
    held = validate(hub.client.get(path, headers=headers()), "routine-occurrence.schema.json")
    assert held["suppressed_by_overlay"] == overlay["overlay"]
    assert held["due_until"] is None
    duration = (end - start) / 2
    hub.advance(start + duration)
    hub.overlay(
        str(overlay["overlay_id"]),
        person_id=str(shown["person_id"]),
        kind=str(overlay["overlay"]),
        state="ended",
        revision=2,
        started_at=start,
        ended_at=hub.clock.now(),
    )
    hub.deliver()
    awake = validate(hub.client.get(path, headers=headers()), "routine-occurrence.schema.json")
    assert awake["suppressed_by_overlay"] is None
    assert instant(awake["due_until"]) == end + duration

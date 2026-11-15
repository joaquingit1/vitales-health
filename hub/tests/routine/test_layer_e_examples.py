"""Independent CONTRACTS 20 vectors and explicitly recorded example defects."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.met import MetLabel
from crew_health.kernel.testing import MemoryMissionClock
from crew_health.kernel.uow import Actor
from crew_health.routine.application.ports import OccurrenceActionBody, OverrideCreateBody, OverrideUntil
from crew_health.routine.application.views import override_view
from crew_health.routine.domain.bindings import Firing, OverlayInput, WarningRaisedInput
from crew_health.routine.domain.vocabulary import MemberBasis, SourceKind

from .harness import RoutineHarness, session, station
from .http_harness import HttpHarness, headers
from .support import REPO
from .test_integration_inputs import at
from .test_query_acceptance import item_occurrence, row_for


def routine_example(name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((REPO / "contracts/api/examples/routine" / (name + ".json")).read_text(encoding="utf-8"))[
            "value"
        ],
    )


def example_occurrence(name: str, item: str, *, state: str | None = None) -> dict[str, object]:
    body = routine_example(name)
    rows = cast(list[dict[str, object]], body.get("items", []))
    for group in cast(list[dict[str, object]], body.get("groups", [])):
        rows.extend(cast(list[dict[str, object]], group["occurrences"]))
    return next(row for row in rows if row["item_id"] == item and (state is None or row["state"] == state))


@pytest.mark.trace("T-E25-020", "CONTRACTS 20")
def test_station_end_texts_reproduce_all_four_contract_vectors(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(8, day=14))
    hub.runtime.assemble(
        replace(hub.engine.deps, mission_clock=InlineClock(hub.mission_clock, "day {day}", 0))
    )
    hub.engine, hub.queries, hub.commands = hub.runtime.engine, hub.runtime.queries, hub.runtime.commands
    hub.commands.act(
        session(),
        item_occurrence(hub, "body_mass"),
        OccurrenceActionBody("complete"),
        if_match=1,
        idempotency_key="mass_day14",
    )
    hub.advance(at(9, 10, day=19))
    hub.engine.startup_sweep()
    reaction = next(
        o
        for o in cast(
            list[dict[str, object]], hub.queries.occurrences(session(), "today", None, None, 100)["items"]
        )
        if o["item_id"] == "reaction_test" and cast(dict[str, object], o["day"])["mission_day"] == 19
    )
    hub.commands.act(
        session(),
        str(reaction["occurrence_id"]),
        OccurrenceActionBody("complete", completed_at=at(7, 38, day=19)),
        if_match=int(str(reaction["revision"])),
        idempotency_key="reaction_0738",
    )
    for item, state in (
        ("blood_pressure", "due"),
        ("vision_self_check", "later"),
        ("reaction_test", "done"),
        ("body_mass", "not_due"),
    ):
        actual = row_for(hub, item)
        expected = example_occurrence("today-station", item, state=state)
        assert actual["end_text"] == expected["end_text"], item


class InlineClock(MemoryMissionClock):
    def __init__(self, original: MemoryMissionClock, template: str, offset: int) -> None:
        super().__init__(original.versions)
        self.template, self.offset = template, offset

    def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
        value = super().met(at, version=version)
        return replace(value, day_label=self.template.format(day=value.mission_day - self.offset))


@pytest.mark.trace("T-E25-020", "CONTRACTS 20")
def test_moon_next_walk_and_mars_first_walk_match_contract_end_texts(tmp_path: Path) -> None:
    for name, env, offset, planned_day, prior in (
        ("today-lunar", "lunar_surface", 15, 21, True),
        ("today-mars", "mars_surface", 16, 24, False),
    ):
        directory = tmp_path / name
        directory.mkdir()
        hub = station(directory, at=at(9, day=19))
        assert hub.mission.facts is not None
        hub.mission.facts = replace(hub.mission.facts, active_environment_id=env)
        # Inline wording comes from the independent environment profile catalog.
        profiles = json.loads((REPO / "config/environment-profiles.json").read_text(encoding="utf-8"))[
            "profiles"
        ]
        template = next(p["day_label"]["inline_template"] for p in profiles if p["environment_id"] == env)
        clock = InlineClock(hub.mission_clock, template, offset)
        hub.runtime.assemble(replace(hub.engine.deps, mission_clock=clock))
        hub.engine, hub.queries, hub.commands = hub.runtime.engine, hub.runtime.queries, hub.runtime.commands
        # CONTRACTS 20's summary vector starts from the producer's existing
        # overlay snapshot, before a walk occurrence has been materialized.
        with hub.db.begin(actor=Actor("engine", "overlay_snapshot"), correlation_id="walk_snapshot") as uow:
            if prior:
                assert hub.engine.deps.store.upsert_overlay(
                    uow,
                    OverlayInput(
                        "ovl_prior",
                        2,
                        "person_mara",
                        "eva",
                        "ended",
                        None,
                        at(7, day=18),
                        at(8, day=18),
                        None,
                    ),
                )
            assert hub.engine.deps.store.upsert_overlay(
                uow,
                OverlayInput(
                    "ovl_next", 1, "person_mara", "eva", "planned", at(10, day=planned_day), None, None, None
                ),
            )
        assert (
            row_for(hub, "pre_eva_check")["end_text"] == example_occurrence(name, "pre_eva_check")["end_text"]
        )


def manual(hub: RoutineHarness) -> dict[str, object]:
    _, result = hub.commands.create_override(
        session("person_sofia", roles=frozenset({Role.CREW_MEDICAL_OFFICER})),
        OverrideCreateBody("vision_self_check", 0, "person_mara", OverrideUntil("person_stops")),
        idempotency_key="manual_golden",
    )
    return result


def stable_override(body: dict[str, object]) -> dict[str, object]:
    dynamic = {"override_id", "person_id", "config_release_id", "started_at", "ended_at"}
    result = {key: value for key, value in body.items() if key not in dynamic}
    result["source"] = {"kind": cast(dict[str, object], body["source"])["kind"]}
    result["ends"] = {"kind": cast(dict[str, object], body["ends"])["kind"]}
    return result


def rendered_override(hub: RoutineHarness, expected: dict[str, object]) -> dict[str, object]:
    """Rebuild producer facts; all wording and provenance come from the release."""
    with hub.db.begin(actor=Actor("engine", "routine_example"), correlation_id=hub.ids.new("cor")) as uow:
        release, mission = hub.engine.release(uow), hub.engine.mission(uow)
        binding = next(b for b in release.bindings if b.binding_id == expected["binding_id"])
        source = cast(dict[str, object], expected["source"])
        inp = WarningRaisedInput(
            str(source["ref_id"]),
            "co2_above_limit",
            str(expected["started_by"]),
            "habitat",
            None,
            "cabin",
            "SIM",
            uow.now,
            None,
            None,
            mission.active_environment_id,
        )
        firing = Firing(
            binding,
            cast("SourceKind", source["kind"]),
            inp.warning_id,
            uow.now,
            "SIM",
            ("person_mara",),
            "cabin",
        )
        group = (("person_mara", "subject" if expected["audience"] == "person" else "site"),)
        override = hub.engine.inputs.new_override(
            uow,
            firing,
            cast("tuple[tuple[str, MemberBasis], ...]", group),
            mission,
            release,
            hub.ids.new("rov"),
            "person_mara" if expected["person_id"] is not None else None,
            str(expected["started_by"]),
        )
        caller = (
            session(roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
            if override.ends_kind == "person_stops"
            else session()
        )
        return override_view(override, caller)


@pytest.mark.trace("T-E25-038", "CONTRACTS 20")
def test_all_environment_override_examples_match_nontext_contract_fields(tmp_path: Path) -> None:
    hub = station(tmp_path)
    for filename in ("overrides-station", "overrides-transit", "overrides-lunar", "overrides-mars"):
        for expected in cast(list[dict[str, object]], routine_example(filename)["items"]):
            actual = rendered_override(hub, expected)
            left, right = stable_override(actual), stable_override(expected)
            left.pop("cadence_text")
            right.pop("cadence_text")
            assert left == right, (filename, expected["item_id"])


@pytest.mark.trace("T-E25-038", "CONTRACTS 20")
def test_manual_override_start_and_end_match_every_stable_example_field(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9, 30, day=19))
    started = manual(hub)
    assert stable_override(started) == stable_override(routine_example("override-manual"))
    hub.advance(at(12, day=19))
    _, ended = hub.commands.end_override(
        session("person_sofia", roles=frozenset({Role.CREW_MEDICAL_OFFICER})),
        str(started["override_id"]),
        if_match=1,
        idempotency_key="manual_golden_end",
    )
    assert stable_override(ended) == stable_override(routine_example("override-ended"))


@pytest.mark.trace("T-E25-038", "CONTRACTS 20")
def test_task_examples_reproduce_configured_instruction_fields(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    tasks = {str(t["item_id"]): t for t in hub.items("/v1/routine/tasks")}
    expected = cast(list[dict[str, object]], routine_example("tasks-station")["items"])
    expected.append(routine_example("task-daily-check"))
    content = (
        "item_id",
        "name",
        "plain",
        "why",
        "who",
        "instrument",
        "minutes",
        "steps",
        "body_region",
        "environment_id",
        "base_cadence",
        "triggers",
    )
    for task in expected:
        actual = tasks[str(task["item_id"])]
        for field in content:
            assert actual[field] == task[field], (task["item_id"], field)


@pytest.mark.trace("T-E25-038", "CONTRACTS 20")
@pytest.mark.xfail(
    strict=True,
    reason="R-E25-11: occurrence-skipped says skipped; CONTRACTS 15 renders the configured reason No time today",
)
def test_skipped_example_end_text_records_the_contract_defect(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    hub.advance(at(9, 20))
    row = hub.occurrence(item="daily_check")
    response = hub.client.post(
        f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
        json={"action": "skip", "skip_reason": "no_time"},
        headers=headers(key="golden_skip", revision=1),
    )
    assert response.status_code == 200
    assert response.json()["end_text"] == "No time today"
    assert response.json()["end_text"] == routine_example("occurrence-skipped")["end_text"]

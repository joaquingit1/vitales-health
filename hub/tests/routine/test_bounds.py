"""Low operational ceilings must page work instead of losing later state."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor

from .harness import RoutineHarness, session, station
from .support import occurrence
from .test_integration_inputs import at, audited


def changed_rules(hub: RoutineHarness, release_id: str, binding_id: str, **changes: object) -> MemoryRelease:
    documents = {
        name: hub.release.document(name)
        for name in (
            ConfigName.ROUTINE_SCHEDULE,
            ConfigName.ROUTINE_RULES,
            ConfigName.UI_COPY,
            ConfigName.QUESTIONNAIRES,
        )
    }
    rules = cast(dict[str, object], json.loads(documents[ConfigName.ROUTINE_RULES].raw))
    bindings = cast(list[dict[str, object]], rules["bindings"])
    binding = next(binding for binding in bindings if binding["binding_id"] == binding_id)
    binding.update(changes)
    raw = canonical_json(rules)
    documents[ConfigName.ROUTINE_RULES] = ConfigDocumentView(
        ConfigName.ROUTINE_RULES, sha256(raw).hexdigest(), raw
    )
    return MemoryRelease(release_id, documents)


def install(hub: RoutineHarness, release: MemoryRelease) -> None:
    hub.release = release
    hub.db.set_release_provider(lambda: release)


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_historical_miss_summary_reads_all_pages(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9, day=3), values={"routine.page_limit": 1})
    base = occurrence()
    with hub.db.begin(actor=Actor("engine", "seed_routine"), correlation_id="cor_miss_history") as uow:
        for index, (item, day) in enumerate(
            (("vision_self_check", 1), ("daily_check", 1), ("daily_check", 2))
        ):
            start = datetime(2026, 11, day, tzinfo=UTC)
            row = replace(
                base,
                occurrence_id=f"ro_miss_{index}",
                person_id="person_mara",
                item_id=item,
                mission_day=day,
                cause_kind="person",
                cause_key=f"seed:{index}",
                periodic=False,
                state="missed",
                due_from=start,
                base_due_until=start + timedelta(hours=1),
                due_until=start + timedelta(hours=1),
                miss_counted=True,
                created_at=start,
                origin_mode="SIM",
            )
            assert hub.engine.deps.store.insert(uow, row)
    with hub.db.snapshot() as view:
        summary = hub.engine.miss_summary(view, "person_mara", hub.engine.release(view), hub.clock.now())
    assert summary.barrier_question_due
    assert summary.item_ids == ("daily_check",)
    assert summary.count == 2


@pytest.mark.trace("T-E25-031", "T-E25-100")
def test_mission_event_end_reads_global_override_pages_and_keeps_original_pin(tmp_path: Path) -> None:
    hub = station(
        tmp_path, at=at(9), values={"routine.page_limit": 1, "routine.max_active_overrides_per_person": 1}
    )
    pinned = changed_rules(
        hub,
        "rel_mission_end",
        "daily_check.after_walk",
        until={"kind": "mission_event", "event_types": ["exercise_ended"]},
    )
    install(hub, pinned)
    for index, person in enumerate(("person_mara", "person_tomas")):
        hub.overlay(
            f"ovl_before_{index}",
            person_id=person,
            kind="eva",
            state="ended",
            revision=2,
            started_at=at(7),
            ended_at=at(9),
        )
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override WHERE state='active'") == ((2,),)
    replacement = changed_rules(
        hub,
        "rel_replacement",
        "daily_check.after_walk",
        until={"kind": "mission_event", "event_types": ["eva_ended"]},
    )
    install(hub, replacement)
    hub.mission_event("exercise_ended", event_id="evt_finish_pinned")
    hub.deliver()
    assert hub.rows(
        "SELECT state,end_reason,config_release_id FROM routine_override ORDER BY override_id"
    ) == (("ended", "mission_event", pinned.release_id), ("ended", "mission_event", pinned.release_id))


@pytest.mark.trace("T-E25-005", "V74")
def test_overlay_overflow_fails_held_and_audits(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(14), values={"routine.max_overlay_intervals": 1, "routine.page_limit": 1})
    target = hub.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check'",
        ("person_mara",),
    )[0][0]
    for index, minute in enumerate((0, 20)):
        hub.overlay(
            f"ovl_short_{index}",
            state="ended",
            revision=2,
            started_at=at(13, minute),
            ended_at=at(13, minute + 10),
        )
    hub.deliver()
    view = hub.queries.occurrence(session(), str(target))
    assert view["state"] == "later"
    assert view["suppressed_by_overlay"] is not None
    assert view["due_until"] is None
    assert audited(hub, "overlay_capacity")


@pytest.mark.trace("T-E25-044", "T-E25-031", "T-E25-093")
def test_supported_five_day_trigger_waits_for_trusted_clock_before_ending(tmp_path: Path) -> None:
    from .test_integration_inputs import drain_timers

    hub = station(tmp_path, at=at(9))
    release = changed_rules(
        hub,
        "rel_five_days",
        "vision_self_check.co2_above_limit",
        until={"kind": "days", "days": 5, "basis": "team default"},
    )
    install(hub, release)
    hub.raise_warning()
    hub.deliver()
    assert hub.rows("SELECT ends_at FROM routine_override WHERE item_id='vision_self_check'") == (
        ("2026-11-06T09:00:00Z",),
    )
    hub.advance(at(9, 1, day=6))
    hub.regress(at(9, day=6))
    drain_timers(hub)
    assert hub.rows("SELECT state FROM routine_override WHERE item_id='vision_self_check'") == (("active",),)
    hub.confirm_time()
    hub.advance(at(9, 2, day=6))
    drain_timers(hub)
    assert hub.rows("SELECT state,end_reason FROM routine_override WHERE item_id='vision_self_check'") == (
        ("ended", "days_elapsed"),
    )


@pytest.mark.trace("T-E25-043", "T-E25-007")
def test_every_minutes_schedules_one_slot_and_clear_adds_its_final_occurrence(tmp_path: Path) -> None:
    from .test_integration_inputs import drain_timers

    hub = station(tmp_path, at=at(9))
    release = changed_rules(
        hub,
        "rel_every_hour",
        "vision_self_check.co2_above_limit",
        effect={
            "kind": "add_occurrences",
            "at_trigger": True,
            "times": [],
            "every_minutes": 60,
            "at_clear": True,
            "basis": "team default",
        },
    )
    install(hub, release)
    hub.raise_warning()
    hub.deliver()
    identifier = hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0]
    assert isinstance(identifier, str)
    assert hub.rows(
        "SELECT COUNT(*) FROM platform_schedule WHERE key=? AND completed=0",
        ("routine.every." + str(identifier),),
    ) == ((1,),)
    hub.advance(at(10))
    drain_timers(hub)
    slots = hub.rows(
        "SELECT cause_key,due_from,due_until FROM routine_occurrence WHERE override_id=? AND person_id=? ORDER BY due_from",
        (identifier, "person_mara"),
    )
    assert slots == (
        (f"override:{identifier}:at_trigger", "2026-11-01T09:00:00Z", "2026-11-01T11:00:00Z"),
        (f"override:{identifier}:every:1", "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z"),
    )
    assert hub.rows(
        "SELECT COUNT(*) FROM platform_schedule WHERE key=? AND completed=0",
        ("routine.every." + str(identifier),),
    ) == ((1,),)
    hub.advance(at(10, 15))
    hub.clear_warning()
    hub.deliver()
    assert hub.rows("SELECT state,end_reason FROM routine_override WHERE override_id=?", (identifier,)) == (
        ("ended", "warning_cleared"),
    )
    assert hub.rows(
        "SELECT due_from,due_until FROM routine_occurrence WHERE override_id=? AND person_id=? AND cause_key=?",
        (identifier, "person_mara", f"override:{identifier}:at_clear"),
    ) == (("2026-11-01T10:15:00Z", "2026-11-01T12:15:00Z"),)
    assert hub.rows(
        "SELECT COUNT(*) FROM platform_schedule WHERE key=? AND completed=0",
        ("routine.every." + str(identifier),),
    ) == ((0,),)

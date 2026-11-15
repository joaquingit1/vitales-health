"""Configuration activation and missing optional inputs through HTTP."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import sleep
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ReleaseHandle
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor
from crew_health.routine.adapters.config_reader import ReleaseCache, RoutineReleaseCheck
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.domain.loader_checks import Bounds
from crew_health.routine.domain.release import RoutineRelease

from .http_harness import HttpHarness, headers, problem, validate
from .support import document


def edited_release(
    hub: HttpHarness, release_id: str, edits: dict[ConfigName, object | None]
) -> MemoryRelease:
    assert isinstance(hub.release, MemoryRelease)
    documents = dict(hub.release.documents)
    for name, value in edits.items():
        if value is None:
            documents.pop(name, None)
        else:
            raw = json.dumps(value).encode("utf-8")
            documents[name] = ConfigDocumentView(name, sha256(raw).hexdigest(), raw)
    return MemoryRelease(release_id, documents)


def activate(hub: HttpHarness, release: MemoryRelease) -> None:
    hub.release = release
    hub.db.set_release_provider(lambda: hub.release)


class DeadlineReleaseGuard(ReleaseCache):
    def release(self, handle: ReleaseHandle) -> RoutineRelease:
        raise AssertionError("A stored deadline must not open presentation documents")


def deadline(hub: HttpHarness, engine: RoutineEngine, identifier: str, at: datetime) -> None:
    hub.advance(at)
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id=hub.ids.new("cor")) as uow:
        engine.occurrence_job(
            uow,
            ScheduledJob(
                "routine.occ." + identifier,
                "routine.occurrence",
                AbsoluteDeadline(at),
                {"occurrence_id": identifier},
                None,
            ),
        )


@pytest.mark.trace("T-E25-025", "T-E25-018", "R-E25-B2")
@pytest.mark.trace("T-E25-103", "3.12.3")
def test_config_edits_change_new_work_and_keep_open_work_pinned(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    body = hub.manual_body()
    item_id = str(body["item_id"])
    old = hub.occurrence(item=item_id)
    old_task = hub.client.get(f"/v1/routine/tasks/{item_id}", headers=headers()).json()
    schedule, rules = document("routine-schedule"), document("routine-rules")
    items = cast(list[dict[str, object]], schedule["items"])
    item = next(i for i in items if i["id"] == item_id)
    cadence = cast(dict[str, object], item["cadence"])
    cadence["default"] = {"word": "daily", "n": None}
    cast(dict[str, object], cadence["by_environment"])["station_leo"] = {"word": "daily", "n": None}
    misses = cast(dict[str, object], rules["misses"])
    assert misses["ask_barrier_after_misses"] == 2
    misses["ask_barrier_after_misses"] = 3
    activate(
        hub,
        edited_release(
            hub, "rel_edited", {ConfigName.ROUTINE_SCHEDULE: schedule, ConfigName.ROUTINE_RULES: rules}
        ),
    )
    new_task = validate(
        hub.client.get(f"/v1/routine/tasks/{item_id}", headers=headers()), "routine-task.schema.json"
    )
    assert new_task["base_cadence"] != old_task["base_cadence"]
    assert new_task["base_cadence"] == {"word": "daily", "n": None}
    pinned = hub.client.get(f"/v1/routine/occurrences/{old['occurrence_id']}", headers=headers())
    assert pinned.json() == old
    assert pinned.headers["Config-Release"] == hub.release.release_id
    path = f"/v1/routine/occurrences/{old['occurrence_id']}/actions"
    problem(
        hub.client.post(
            path,
            json={"action": "complete"},
            headers=headers(key="wrong_pinned_release", revision=1, release="rel_neither"),
        ),
        "CONFIG_VERSION_MISMATCH",
        409,
    )
    complete = hub.client.post(
        path,
        json={"action": "complete"},
        headers=headers(key="old_pinned_release", revision=1, release=str(old["config_release_id"])),
    )
    assert complete.status_code == 200
    another = hub.occurrence()
    assert (
        hub.client.post(
            f"/v1/routine/occurrences/{another['occurrence_id']}/actions",
            json={"action": "complete"},
            headers=headers(
                key="active_release_key",
                revision=int(str(another["revision"])),
                release=hub.release.release_id,
            ),
        ).status_code
        == 200
    )
    hub.advance(hub.clock.now() + timedelta(days=1))
    hub.restart(hub.engine.deps)
    fresh = hub.occurrence(item=item_id)
    assert fresh["config_release_id"] == hub.release.release_id
    assert fresh["occurrence_id"] != old["occurrence_id"]
    assert hub.engine.deps.releases.release(hub.release).misses.ask_barrier_after_misses == 3


@pytest.mark.trace("T-E25-025", "T-E25-014")
def test_missing_and_corrupt_rules_fail_reads_but_stored_deadlines_survive_a_cold_cache(
    tmp_path: Path,
) -> None:
    for index, value in enumerate((None, {"notice": "corrupt rules"})):
        directory = tmp_path / str(index)
        directory.mkdir()
        hub = HttpHarness(directory)
        row = hub.occurrence()
        identifier = str(row["occurrence_id"])
        release = edited_release(hub, f"rel_broken_{index}", {ConfigName.ROUTINE_RULES: value})
        check = RoutineReleaseCheck(Bounds(100, 100, 32))
        assert check.check(CandidateRelease(release.release_id, release.documents))
        activate(hub, release)
        problem(hub.client.get("/v1/routine/today", headers=headers()), "CONFIG_INVALID", 422)
        cold = RoutineEngine(replace(hub.engine.deps, releases=DeadlineReleaseGuard(8, Bounds(100, 100, 32))))
        deadline(hub, cold, identifier, datetime.fromisoformat(str(row["due_until"]).replace("Z", "+00:00")))
        assert hub.rows(
            "SELECT state,miss_counted FROM routine_occurrence WHERE occurrence_id=?", (identifier,)
        ) == (("missed", 1),)


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_missing_ui_copy_serves_identifiers_and_commands_commit_with_degraded_health(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    activate(hub, edited_release(hub, "rel_without_words", {ConfigName.UI_COPY: None}))
    hub.advance(hub.clock.now() + timedelta(days=1))
    hub.restart(hub.engine.deps)
    schedule = validate(
        hub.client.get("/v1/routine/today", headers=headers()), "routine-schedule.schema.json"
    )
    assert schedule["groups"]
    for group in cast(list[dict[str, object]], schedule["groups"]):
        assert group["label"] == group["group_id"]
    row = hub.occurrence()
    response = hub.client.post(
        f"/v1/routine/occurrences/{row['occurrence_id']}/actions",
        json={"action": "complete"},
        headers=headers(key="no_words_complete", revision=int(str(row["revision"]))),
    )
    assert validate(response, "routine-occurrence.schema.json")["state"] == "done"
    assert any(
        c.component == "routine" and c.code == "CONFIG_INVALID" for c in hub.health.snapshot().components
    )


@pytest.mark.trace("T-E25-014", "T-E25-027")
def test_missing_roster_is_audited_without_creating_location_overrides(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    hub.restart(replace(hub.engine.deps, roster=None))
    hub.raise_warning()
    hub.deliver()
    assert hub.items("/v1/routine/overrides") == []
    assert "routine.roster_unavailable" in str(hub.rows("SELECT envelope FROM platform_audit"))


@pytest.mark.trace("T-E25-014")
def test_missing_mission_does_not_stop_misses_and_today_is_unavailable(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    hub.restart(replace(hub.engine.deps, mission=None))
    problem(hub.client.get("/v1/routine/today", headers=headers()), "CAPABILITY_UNAVAILABLE", 422)
    deadline(
        hub,
        hub.engine,
        str(row["occurrence_id"]),
        datetime.fromisoformat(str(row["due_until"]).replace("Z", "+00:00")),
    )
    assert hub.rows(
        "SELECT state FROM routine_occurrence WHERE occurrence_id=?", (str(row["occurrence_id"]),)
    ) == (("missed",),)


class SlowLocations:
    def __init__(self, delay: float) -> None:
        self.calls = 0
        self.delay = delay

    def location_of(self, person_id: str, at: datetime) -> str | None:
        self.calls += 1
        sleep(self.delay)
        return None


@pytest.mark.trace("T-E25-014", "INV-E25-16")
def test_model_devices_ground_and_guidance_are_absent_and_slow_location_is_not_on_miss_path(
    tmp_path: Path,
) -> None:
    # The layer B station registers none of the assistant, device, ground or guidance ports.
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    slow = SlowLocations((hub.settings.integer("storage.transaction_timeout_ms") + 1) / 1000)
    hub.restart(replace(hub.engine.deps, locations=slow))
    deadline(
        hub,
        hub.engine,
        str(row["occurrence_id"]),
        datetime.fromisoformat(str(row["due_until"]).replace("Z", "+00:00")),
    )
    assert slow.calls == 0
    assert hub.rows(
        "SELECT state,miss_counted FROM routine_occurrence WHERE occurrence_id=?",
        (str(row["occurrence_id"]),),
    ) == (("missed", 1),)
    validate(hub.client.get("/v1/routine/today", headers=headers()), "routine-schedule.schema.json")


@pytest.mark.trace("T-E25-025", "T-E25-014")
def test_rules_edit_changes_the_served_barrier_threshold_for_new_work(tmp_path: Path) -> None:
    observed: list[bool] = []
    # The edited run moves a day forward to start new work under the new rules; that day's own miss counts (B1),
    # so it sees three counted misses against the edited threshold of four, the base run two against two.
    for threshold in (2, 4):
        directory = tmp_path / str(threshold)
        directory.mkdir()
        hub = HttpHarness(directory)
        if threshold == 4:
            rules = document("routine-rules")
            cast(dict[str, object], rules["misses"])["ask_barrier_after_misses"] = threshold
            activate(hub, edited_release(hub, "rel_threshold", {ConfigName.ROUTINE_RULES: rules}))
            hub.advance(hub.clock.now() + timedelta(days=1))
            hub.restart(hub.engine.deps)
        # A configured daily item with an all-day window produces one real miss per day.
        with hub.db.snapshot() as view:
            release = hub.engine.release(view)
            item = release.items[0]
            assert item.default_cadence.word == "daily"
        for _ in range(2):
            row = hub.occurrence(item=item.item_id)
            deadline(
                hub,
                hub.engine,
                str(row["occurrence_id"]),
                datetime.fromisoformat(str(row["due_until"]).replace("Z", "+00:00")),
            )
            hub.restart(hub.engine.deps)
        schedule = validate(
            hub.client.get("/v1/routine/today", headers=headers()), "routine-schedule.schema.json"
        )
        observed.append(bool(cast(dict[str, object], schedule["misses"])["barrier_question_due"]))
    assert observed == [True, False]


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_failed_day_materialization_does_not_block_the_real_scheduler_from_recording_misses(
    tmp_path: Path,
) -> None:
    for fault in ("rules", "mission"):
        directory = tmp_path / fault
        directory.mkdir()
        hub = HttpHarness(directory)
        row = hub.occurrence()
        if fault == "rules":
            activate(
                hub, edited_release(hub, "rel_missing_rules_scheduler", {ConfigName.ROUTINE_RULES: None})
            )
            cold = RoutineEngine(replace(hub.engine.deps, releases=ReleaseCache(8, Bounds(100, 100, 32))))
            hub.db.scheduler.handlers["routine.occurrence"] = cold.occurrence_job
            hub.db.scheduler.handlers["routine.day"] = cold.day_job
        else:
            hub.restart(replace(hub.engine.deps, mission=None))
        hub.advance(datetime.fromisoformat(str(row["due_until"]).replace("Z", "+00:00")))
        assert hub.dispatch() > 0
        assert hub.rows(
            "SELECT state,miss_counted FROM routine_occurrence WHERE occurrence_id=?",
            (str(row["occurrence_id"]),),
        ) == (("missed", 1),)

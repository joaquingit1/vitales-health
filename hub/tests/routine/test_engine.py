from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.kernel.clock import ClockCorrected, ClockRegressed
from crew_health.kernel.uow import Actor
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.domain.occurrence import Occurrence

from .harness import RoutineHarness, session, station


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def occurrence_for(hub: RoutineHarness, item: str, day: int = 1) -> Occurrence:
    with hub.db.snapshot() as view:
        found = hub.engine.deps.store.periodic_on_day(view, "person_mara", item, day)
        assert found is not None
        return found


def drain(hub: RoutineHarness) -> None:
    for _ in range(
        hub.settings.integer("scheduler.max_jobs") // hub.settings.integer("scheduler.batch_size") + 1
    ):
        if hub.dispatch() < hub.settings.integer("scheduler.batch_size"):
            return
    raise AssertionError("scheduler failed to quiesce")


@pytest.mark.trace("T-E25-001", "T-E25-014")
def test_materialization_preserves_future_window_and_day_job_stores_today(tmp_path: Path) -> None:
    hub = station(tmp_path)
    exercise = occurrence_for(hub, "exercise_log")
    assert exercise.state == "later"
    assert exercise.created_at == hub.clock.now()
    assert hub.queries.occurrence(session(), exercise.occurrence_id)["state"] == "later"
    hub.advance(at(2))
    drain(hub)
    assert occurrence_for(hub, "exercise_log", 2).state == "later"
    assert hub.rows(
        "SELECT COUNT(*) FROM routine_occurrence WHERE person_id=? AND item_id=? AND mission_day=2",
        ("person_mara", "exercise_log"),
    ) == ((1,),)


@pytest.mark.trace("T-E25-014", "AGENTS-1-3")
def test_miss_runs_with_only_store_clock_scheduler(tmp_path: Path) -> None:
    hub = station(tmp_path)
    exercise = occurrence_for(hub, "exercise_log")
    # Scheduled misses must not consult any optional mission, roster or location provider.
    engine = RoutineEngine(replace(hub.engine.deps, mission=None, roster=None, locations=None))
    hub.db.scheduler.handlers["routine.occurrence"] = engine.occurrence_job
    hub.advance(at(1, 17))
    drain(hub)
    current = occurrence_for(hub, "exercise_log")
    assert current.state == "missed" and current.miss_counted is True
    assert hub.rows(
        "SELECT COUNT(*) FROM routine_occurrence_transition WHERE occurrence_id=? AND to_state=?",
        (exercise.occurrence_id, "missed"),
    ) == ((1,),)


@pytest.mark.trace("T-E25-016", "INV-E25-11")
def test_restart_skips_unseen_day_and_moves_overdue_weekly_slot_to_today(tmp_path: Path) -> None:
    hub = station(tmp_path)
    first = occurrence_for(hub, "vision_self_check")
    hub.advance(at(1, 15))
    drain(hub)
    assert occurrence_for(hub, "vision_self_check").miss_counted is True
    hub.db.close()
    restarted = station(tmp_path, at=at(10), initialize=False)
    drain(restarted)
    assert occurrence_for(restarted, "vision_self_check", 10).mission_day == 10
    assert restarted.rows("SELECT COUNT(*) FROM routine_occurrence WHERE mission_day BETWEEN 2 AND 9") == (
        (0,),
    )
    assert restarted.rows(
        "SELECT miss_counted FROM routine_occurrence WHERE occurrence_id=?", (first.occurrence_id,)
    ) == ((1,),)


@pytest.mark.trace("T-E25-017", "INV-E25-11")
def test_forward_jump_records_uncounted_miss_and_read_backup_works(tmp_path: Path) -> None:
    hub = station(tmp_path)
    exercise = occurrence_for(hub, "exercise_log")
    hub.advance(at(1, 20))
    assert hub.queries.occurrence(session(), exercise.occurrence_id)["state"] == "missed"
    drain(hub)
    current = occurrence_for(hub, "exercise_log")
    assert current.state == "missed" and current.miss_counted is False
    drain(hub)
    assert occurrence_for(hub, "exercise_log").revision == current.revision


@pytest.mark.trace("T-E25-093")
def test_regressed_clock_marks_misses_uncounted_until_corrected(tmp_path: Path) -> None:
    hub = station(tmp_path)
    with hub.db.begin(actor=Actor("engine", "clock"), correlation_id="cor_regress") as uow:
        uow.publish(ClockRegressed(at(1), at(1, 1), 3600000))
    hub.advance(at(1, 17))
    drain(hub)
    assert occurrence_for(hub, "exercise_log").miss_counted is False
    with hub.db.begin(actor=Actor("engine", "clock"), correlation_id="cor_correct") as uow:
        uow.publish(ClockCorrected(0, 0, at(1, 17), 1, "operator"))
    hub.advance(at(2))
    drain(hub)
    hub.advance(at(2, 17))
    drain(hub)
    assert occurrence_for(hub, "exercise_log", 2).miss_counted is True


@pytest.mark.trace("T-E25-093", "T-E25-015")
def test_backward_correction_does_not_duplicate_materialized_day(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(2, 8))
    before = hub.rows("SELECT occurrence_id,state FROM routine_occurrence ORDER BY occurrence_id")
    hub.advance(at(1, 23))
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_backward") as uow:
        hub.engine.materialize(uow, "person_mara", 2)
    assert hub.rows("SELECT occurrence_id,state FROM routine_occurrence ORDER BY occurrence_id") == before
    hub.advance(at(2, 8))
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_forward") as uow:
        hub.engine.materialize(uow, "person_mara", 2)
    assert hub.rows("SELECT occurrence_id,state FROM routine_occurrence ORDER BY occurrence_id") == before


@pytest.mark.trace("T-E25-109")
def test_successor_closes_earlier_periodic_uncounted(tmp_path: Path) -> None:
    hub = station(tmp_path)
    old = occurrence_for(hub, "exercise_log")
    hub.advance(at(2, 1))
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_successor") as uow:
        hub.engine.materialize(uow, "person_mara", 2)
    prior = occurrence_for(hub, "exercise_log")
    assert prior.state == "missed" and prior.miss_counted is False
    assert prior.end_reason == "superseded_by_next_slot"
    assert occurrence_for(hub, "exercise_log", 2).periodic
    assert hub.rows(
        "SELECT reason_code FROM routine_occurrence_transition WHERE occurrence_id=? AND revision=2",
        (old.occurrence_id,),
    ) == (("superseded_by_next_slot",),)


@pytest.mark.trace("T-E25-015", "T-E25-022")
def test_replaying_inputs_into_fresh_stations_has_identical_rows(tmp_path: Path) -> None:
    rows = []
    for name in ("first", "second"):
        hub = station(tmp_path / name)
        hub.raise_warning()
        hub.deliver()
        hub.raise_warning()
        hub.deliver()
        rows.append(
            (
                hub.rows("SELECT * FROM routine_occurrence ORDER BY occurrence_id"),
                hub.rows("SELECT * FROM routine_override ORDER BY override_id"),
            )
        )
    assert rows[0] == rows[1]


@pytest.mark.trace("T-E25-005", "V74")
def test_planned_overlay_never_holds_and_exact_interval_bound_is_supported(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 15), values={"routine.max_overlay_intervals": 1})
    exercise = occurrence_for(hub, "exercise_log")
    hub.overlay("ovl_planned_sleep", state="planned", planned_start=at(1, 16))
    hub.deliver()
    planned = hub.queries.occurrence(session(), exercise.occurrence_id)
    assert planned["suppressed_by_overlay"] is None
    assert planned["due_until"] == "2026-11-01T17:00:00Z"
    hub.advance(at(1, 15, 15))
    hub.overlay("ovl_planned_sleep", revision=2, state="ended", started_at=at(1, 15), ended_at=at(1, 15, 15))
    hub.deliver()
    ended = hub.queries.occurrence(session(), exercise.occurrence_id)
    assert ended["suppressed_by_overlay"] is None
    assert ended["due_until"] == "2026-11-01T17:15:00Z"

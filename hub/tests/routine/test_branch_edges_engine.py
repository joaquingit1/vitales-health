"""Engine edges: elapsed timer lateness, absent clock trust and roster, held deadlines and unreadable pins."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from crew_health.kernel.scheduler import AbsoluteDeadline, ElapsedDeadline, ScheduledJob
from crew_health.kernel.uow import Actor

from .harness import RoutineHarness, station
from .test_engine import at, drain, occurrence_for

ENGINE = Actor("engine", "engine_routine")


def day_rows(hub: RoutineHarness, day: int) -> int:
    count = hub.rows(
        "SELECT COUNT(*) FROM routine_occurrence WHERE person_id='person_mara' AND mission_day=?", (day,)
    )[0][0]
    assert isinstance(count, int)
    return count


def test_an_elapsed_timer_measures_its_lateness_through_the_shared_clock(tmp_path: Path) -> None:
    hub = station(tmp_path)
    with hub.db.begin(actor=ENGINE, correlation_id="cor_late") as uow:
        anchor = hub.engine.hub_time(uow)
        assert anchor is not None
        elapsed = ScheduledJob(
            "routine.test", "routine.occurrence", ElapsedDeadline(anchor, timedelta(hours=1)), {}, None
        )
        absolute = ScheduledJob("routine.test", "routine.occurrence", AbsoluteDeadline(uow.now), {}, None)
        # No time has passed since the anchor: the timer is one hour early, never late.
        assert hub.engine.lateness(uow, elapsed) == -timedelta(hours=1)
        assert hub.engine.lateness(uow, absolute) is None


def test_without_clock_trust_no_miss_ever_counts(tmp_path: Path) -> None:
    hub = station(tmp_path)
    assert hub.trusted()
    hub.restart(replace(hub.engine.deps, clock_trust=None))
    with hub.db.begin(actor=ENGINE, correlation_id="cor_trust") as uow:
        assert hub.engine.trusted(uow) is False
        assert hub.engine.counted(uow, uow.now) is False


def test_an_environment_activation_without_a_roster_materializes_nobody(tmp_path: Path) -> None:
    hub = station(tmp_path)
    hub.advance(at(2))
    hub.restart(replace(hub.engine.deps, roster=None))
    with hub.db.begin(actor=ENGINE, correlation_id="cor_env_none") as uow:
        hub.engine.environment_activated(uow)
    assert day_rows(hub, 2) == 0
    hub.restart(replace(hub.engine.deps, roster=hub.roster))
    with hub.db.begin(actor=ENGINE, correlation_id="cor_env_roster") as uow:
        hub.engine.environment_activated(uow)
    assert day_rows(hub, 2) > 0


def test_inserting_a_stored_occurrence_again_is_refused(tmp_path: Path) -> None:
    hub = station(tmp_path)
    stored = occurrence_for(hub, "exercise_log")
    with hub.db.begin(actor=ENGINE, correlation_id="cor_insert") as uow:
        assert hub.engine.insert(uow, stored) is False
    assert hub.rows(
        "SELECT COUNT(*) FROM routine_occurrence WHERE occurrence_id=?", (stored.occurrence_id,)
    ) == ((1,),)


def test_a_held_occurrence_has_no_deadline_to_schedule_or_miss(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 9))
    drain(hub)
    hub.overlay(state="active", started_at=hub.clock.now())
    hub.deliver()
    held = occurrence_for(hub, "exercise_log")
    assert held.due_until is None and held.state not in ("missed", "done", "skipped")
    key = "routine.occ." + held.occurrence_id
    with hub.db.snapshot() as view:
        assert hub.engine.deps.store.deadline_generation(view, key) is not None
    hub.advance(at(2, 12))
    job = ScheduledJob(
        key, "routine.occurrence", AbsoluteDeadline(at(1, 17)), {"occurrence_id": held.occurrence_id}, None
    )
    with hub.db.begin(actor=ENGINE, correlation_id="cor_held") as uow:
        hub.engine.occurrence_job(uow, job)
        hub.engine.deadline_miss(uow, held)
        generation = hub.engine.deps.store.deadline_generation(uow, key)
        hub.engine.schedule_deadline(uow, held)
        assert hub.engine.deps.store.deadline_generation(uow, key) == generation
    after = occurrence_for(hub, "exercise_log")
    assert (after.state, after.revision, after.due_until) == (held.state, held.revision, None)


def test_an_unreadable_pin_keeps_the_stored_deadline_and_a_held_occurrence_stays_unscheduled(
    tmp_path: Path,
) -> None:
    hub = station(tmp_path)
    stored = occurrence_for(hub, "exercise_log")
    assert stored.due_until is not None
    orphan = replace(stored, config_release_id="rel_evicted")
    key = "routine.occ." + stored.occurrence_id
    with hub.db.begin(actor=ENGINE, correlation_id="cor_pin") as uow:
        generation = hub.engine.deps.store.deadline_generation(uow, key)
        hub.engine.reschedule(uow, replace(orphan, due_until=None))
        assert hub.engine.deps.store.deadline_generation(uow, key) == generation
        hub.engine.reschedule(uow, orphan)
        kept = hub.engine.deps.store.deadline_generation(uow, key)
        assert kept is not None and generation is not None and kept > generation
    assert any(
        c.component == "routine" and c.code == "CONFIG_INVALID" for c in hub.health.snapshot().components
    )


def test_an_override_on_an_uncached_release_is_not_projected_and_marks_routine_degraded(
    tmp_path: Path,
) -> None:
    from .support import override

    hub = station(tmp_path)
    before = hub.rows("SELECT COUNT(*) FROM events_outbox")
    with hub.db.begin(actor=ENGINE, correlation_id="cor_emit") as uow:
        hub.engine.emit_override(uow, replace(override(), config_release_id="rel_evicted"))
    assert hub.rows("SELECT COUNT(*) FROM events_outbox") == before
    assert any(
        c.component == "routine" and c.code == "CONFIG_INVALID" for c in hub.health.snapshot().components
    )

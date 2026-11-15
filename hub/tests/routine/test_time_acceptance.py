from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import Actor
from crew_health.routine.adapters.config_reader import ReleaseCache
from crew_health.routine.domain.errors import ConfigInvalid, MissionUnavailable

from .harness import RoutineHarness, configured_release, session, station


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


@pytest.mark.trace("T-E25-014", "T-E25-017")
def test_early_deadline_and_final_duplicate_callback_preserve_the_record(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 8))
    with hub.db.snapshot() as view:
        occ = hub.engine.deps.store.periodic_on_day(view, "person_mara", "exercise_log", 1)
        assert occ is not None and occ.due_until is not None
        generation = hub.engine.deps.store.deadline_generation(view, "routine.occ." + occ.occurrence_id)
    job = ScheduledJob(
        "routine.occ." + occ.occurrence_id,
        "routine.occurrence",
        AbsoluteDeadline(at(1, 8)),
        {"occurrence_id": occ.occurrence_id},
        None,
    )
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_early") as uow:
        hub.engine.occurrence_job(uow, job)
    with hub.db.snapshot() as view:
        assert hub.engine.deps.store.get(view, occ.occurrence_id) == occ
        assert hub.engine.deps.store.deadline_generation(view, job.key) == (generation or 0) + 1
    hub.advance(at(1, 17))
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_ontime") as uow:
        hub.engine.occurrence_job(uow, job)
    before = hub.rows(
        "SELECT * FROM routine_occurrence_transition WHERE occurrence_id=?", (occ.occurrence_id,)
    )
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_duplicate") as uow:
        hub.engine.occurrence_job(uow, job)
    assert (
        hub.rows("SELECT * FROM routine_occurrence_transition WHERE occurrence_id=?", (occ.occurrence_id,))
        == before
    )
    assert hub.queries.occurrence(session(), occ.occurrence_id)["state"] == "missed"


@pytest.mark.trace("INV-E25-11", "INV-E25-16")
def test_unavailable_mission_aborts_materialization_atomically(tmp_path: Path) -> None:
    hub = station(tmp_path)
    before = hub.rows("SELECT * FROM routine_occurrence ORDER BY occurrence_id")
    hub.mission.facts = None
    with pytest.raises(MissionUnavailable):
        with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_no_mission") as uow:
            hub.engine.materialize(uow, "person_mara", 2)
    assert hub.rows("SELECT * FROM routine_occurrence ORDER BY occurrence_id") == before
    # No mission facts is a missing capability, not a fault (SYSTEM 5.10): refused, health stays ok.
    assert not [c for c in hub.health.snapshot().components if c.code]


@pytest.mark.trace("T-E25-005", "T-E25-091")
def test_before_walk_keeps_source_day_and_its_walk_start_closes_it(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(2, 8))
    hub.overlay("ovl_eva_1", kind="eva", state="planned", planned_start=at(2, 10))
    hub.deliver()
    row = hub.rows(
        "SELECT occurrence_id,mission_day,due_from,due_until FROM routine_occurrence WHERE person_id=? AND item_id='pre_eva_check'",
        ("person_mara",),
    )[0]
    assert row[1:] == (1, "2026-11-01T10:00:00Z", "2026-11-02T10:00:00Z")
    hub.advance(at(2, 10))
    hub.overlay(
        "ovl_eva_1", revision=2, kind="eva", state="active", planned_start=at(2, 10), started_at=at(2, 10)
    )
    hub.deliver()
    assert hub.rows(
        "SELECT state,miss_counted,end_reason FROM routine_occurrence WHERE occurrence_id=?", (row[0],)
    ) == (("missed", 1, "walk_started"),)
    hub.advance(at(2, 12))
    hub.overlay("ovl_eva_1", revision=3, kind="eva", state="ended", started_at=at(2, 10), ended_at=at(2, 12))
    hub.deliver()
    assert hub.rows(
        "SELECT mission_day,due_from,due_until FROM routine_occurrence WHERE person_id=? AND item_id='post_eva_check'",
        ("person_mara",),
    ) == ((2, "2026-11-02T12:00:00Z", "2026-11-02T16:00:00Z"),)


@pytest.mark.trace("T-E25-014", "INV-E25-03")
def test_environment_anchor_is_rejected_without_environment_start_fact(tmp_path: Path) -> None:
    hub = station(tmp_path)
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
        mission = hub.mission.current()
        assert mission is not None
        unsupported = replace(release, calendar=replace(release.calendar, anchor="environment_start"))
        with pytest.raises(ConfigInvalid) as error:
            hub.engine.periodic(view, "person_mara", 1, unsupported, mission)
        assert error.value.findings[0].pointer == "/calendar/anchor"
        assert "E23" in error.value.findings[0].message


@pytest.mark.trace("T-E25-014", "SYSTEM-5-11")
def test_supported_open_bound_above_storage_page_size_still_materializes(tmp_path: Path) -> None:
    hub = station(tmp_path, values={"routine.max_open_per_person": 2000})
    # Seams this harness leaves absent are missing capabilities, never faults: health stays ok.
    assert not [c for c in hub.health.snapshot().components if c.code]
    assert hub.rows("SELECT COUNT(*) FROM routine_occurrence")[0][0] != 0
    schedule = hub.queries.schedule(session(), "today")
    assert schedule["person_id"] == "person_mara"
    hub.advance(at(2))
    for _ in range(10):
        if not hub.dispatch():
            break
    assert hub.rows("SELECT COUNT(*) FROM routine_occurrence WHERE mission_day=2")[0][0] != 0


@pytest.mark.trace("T-E25-109", "T-E25-007")
def test_tighter_warning_cadence_closes_previous_periodic_without_counting_it(tmp_path: Path) -> None:
    hub = station(tmp_path)
    old = hub.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check'",
        ("person_mara",),
    )[0][0]
    # No scheduler pass occurred during this gap. The tightening must close the old slot itself.
    hub.advance(at(2, 9))
    hub.raise_warning()
    hub.deliver()
    # B1: the old slot's window ended at the day boundary, so the tightening decides it as its deadline would;
    # decided nine hours late it is still not counted.
    assert hub.rows(
        "SELECT state,miss_counted,end_reason FROM routine_occurrence WHERE occurrence_id=?", (old,)
    ) == (("missed", 0, "deadline"),)
    assert hub.rows(
        "SELECT mission_day,cause_kind,periodic FROM routine_occurrence WHERE person_id=? AND item_id='vision_self_check' AND mission_day=2",
        ("person_mara",),
    ) == ((2, "override", 1),)


@pytest.mark.trace("INV-E25-07", "T-E25-014")
def test_unavailable_historical_pin_fails_closed_instead_of_substituting_content(tmp_path: Path) -> None:
    from crew_health.routine.adapters.config_reader import ReleaseCache
    from crew_health.routine.application.engine import RoutineEngine
    from crew_health.routine.application.queries import RoutineQueryService

    from .harness import configured_release

    hub = station(tmp_path)
    identifier = hub.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id='daily_check'",
        ("person_mara",),
    )[0][0]
    assert isinstance(identifier, str)
    cache = ReleaseCache(1)
    cache.release(hub.release)
    next_release = configured_release("rel_later")
    cache.release(next_release)
    hub.db.set_release_provider(lambda: next_release)
    deps = replace(hub.engine.deps, releases=cache)
    queries = RoutineQueryService(deps, RoutineEngine(deps))
    with pytest.raises(ConfigInvalid) as error:
        queries.occurrence(session(), identifier)
    assert error.value.findings[0].pointer == "/config_release_id"


class _History:
    """A kernel ConfigReleaseReader that retained earlier release versions."""

    def __init__(self, *handles: ReleaseHandle) -> None:
        self.handles = {h.release_id: h for h in handles}

    def active(self) -> ReleaseHandle:
        raise AssertionError("routine never asks the history reader for the active release")

    def release(self, release_id: str) -> ReleaseHandle:
        return self.handles[release_id]


def _evict_pin(hub: RoutineHarness, history: _History | None) -> None:
    """Activate a newer release and leave the pinned one out of a one entry cache, as after a restart."""
    next_release = configured_release("rel_later")
    cache = ReleaseCache(1, history=history)
    cache.release(next_release)
    hub.db.set_release_provider(lambda: next_release)
    hub.engine.deps = replace(hub.engine.deps, releases=cache)


@pytest.mark.trace("T-E25-014", "INV-E25-11", "PLAN-row-3")
def test_a_deadline_records_its_miss_when_the_pinned_release_cannot_be_read(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 8))
    with hub.db.snapshot() as view:
        occ = hub.engine.deps.store.periodic_on_day(view, "person_mara", "exercise_log", 1)
        assert occ is not None and occ.due_until is not None
    _evict_pin(hub, None)
    hub.advance(occ.due_until)
    hub.dispatch()
    with hub.db.snapshot() as view:
        missed = hub.engine.deps.store.get(view, occ.occurrence_id)
    assert missed is not None and missed.state == "missed" and missed.miss_counted is True
    assert missed.config_release_id == occ.config_release_id
    assert any(c.code == "CONFIG_INVALID" for c in hub.health.snapshot().components)


@pytest.mark.trace("PLAN-row-3", "R-E25-B2")
def test_open_work_keeps_its_pinned_release_after_a_restart_through_the_release_reader(
    tmp_path: Path,
) -> None:
    hub = station(tmp_path, at=at(1, 8))
    with hub.db.snapshot() as view:
        occ = hub.engine.deps.store.periodic_on_day(view, "person_mara", "exercise_log", 1)
        assert occ is not None
    _evict_pin(hub, _History(hub.release))
    with hub.db.snapshot() as view:
        assert hub.engine.release_for(view, occ.config_release_id).release_id == hub.release.release_id
    assert hub.queries.occurrence(session(), occ.occurrence_id)["config_release_id"] == occ.config_release_id

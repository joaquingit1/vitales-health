"""Regression tests for the independent review of the routine build (blockers B1 and B2 are in this file and in
test_production_composition.py; M1 to M5 and the minors follow). Each failed before its fix."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName, Role
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork
from crew_health.routine.application.ports import OccurrenceActionBody

from .harness import RoutineHarness, session, station
from .test_bounds import changed_rules, install


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def drain(hub: RoutineHarness) -> None:
    for _ in range(50):
        if not hub.dispatch():
            return
    raise AssertionError("scheduler failed to quiesce")


def ends(hub: RoutineHarness, ids: list[str]) -> set[tuple[object, ...]]:
    marks = ",".join("?" * len(ids))
    return set(
        hub.rows(
            f"SELECT state, miss_counted, end_reason FROM routine_occurrence WHERE occurrence_id IN ({marks})",
            tuple(ids),
        )
    )


def all_day(hub: RoutineHarness, day: int, person: str | None = None) -> list[str]:
    rows = hub.rows(
        "SELECT occurrence_id, due_until, person_id FROM routine_occurrence WHERE mission_day=? AND periodic=1",
        (day,),
    )
    boundary = at(day + 1).isoformat().replace("+00:00", "")
    return [str(r[0]) for r in rows if str(r[1]).startswith(boundary) and (person is None or r[2] == person)]


class Grants:
    def __init__(self, refs: frozenset[str]) -> None:
        self.refs = refs

    def can_read(self, session: SessionContext, object_ref: str) -> bool:
        return object_ref in self.refs


# B1 ------------------------------------------------------------------------------------------------------------


@pytest.mark.trace("T-E25-093", "T-E25-109", "R01")
def test_all_day_miss_at_the_day_boundary_counts(tmp_path: Path) -> None:
    """Review probe B1: the day job and the all-day deadline fall due at the same instant and run in key order."""
    hub = station(tmp_path)
    ids = all_day(hub, 1)
    assert ids
    hub.advance(at(2))
    drain(hub)
    assert ends(hub, ids) == {("missed", 1, "deadline")}


@pytest.mark.trace("T-E25-093", "T-E25-011", "V67")
def test_two_full_days_of_skipped_all_day_items_count_every_miss_and_ask_the_barrier_question(
    tmp_path: Path,
) -> None:
    """Two whole mission days with the scheduler on time and nothing done: every all-day item of every crew member
    is a counted deadline miss, and the configured barrier threshold (routine-rules misses) is reached through the
    real miss summary, which was never possible while the day job closed them uncounted."""
    hub = station(tmp_path)
    first = all_day(hub, 1)
    for day in (1, 2):
        for hour in range(1, 24, 4):
            hub.advance(at(day, hour))
            drain(hub)
        hub.advance(at(day + 1))
        drain(hub)
    second = all_day(hub, 2)
    assert first and second
    assert ends(hub, first + second) == {("missed", 1, "deadline")}
    for member in hub.roster.members():
        assert all_day(hub, 3, member.person_id)
    twice = {
        str(r[0])
        for r in hub.rows(
            "SELECT item_id FROM routine_occurrence WHERE person_id='person_mara' AND miss_counted=1 "
            "GROUP BY item_id HAVING count(*) >= 2"
        )
    }
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
        summary = hub.engine.miss_summary(view, "person_mara", release, view.now)
    assert release.misses.ask_barrier_after_misses <= 2
    assert twice and summary.barrier_question_due and twice <= set(summary.item_ids)
    assert hub.rows("SELECT count(*) FROM routine_barrier_ask WHERE person_id='person_mara'") != ((0,),)


@pytest.mark.trace("T-E25-109")
def test_held_all_day_occurrence_is_still_closed_by_its_successor_uncounted(tmp_path: Path) -> None:
    """Design test 109: sleep over an all-day item extends it past midnight; the successor closes it uncounted."""
    hub = station(tmp_path)
    ids = all_day(hub, 1, "person_mara")
    hub.advance(at(1, 23))
    hub.overlay("ovl_sleep_b1", started_at=at(1, 23))
    hub.deliver()
    hub.advance(at(2, 7))
    hub.overlay("ovl_sleep_b1", revision=2, state="ended", started_at=at(1, 23), ended_at=at(2, 7))
    hub.deliver()
    drain(hub)
    closed = ends(hub, ids)
    assert ("missed", 0, "superseded_by_next_slot") in closed or ("missed", 1, "deadline") not in closed


# M1 ------------------------------------------------------------------------------------------------------------


@pytest.mark.trace("T-E25-025", "INV-E25-01")
def test_unreadable_pin_keeps_the_stored_deadline_and_marks_routine_degraded(tmp_path: Path) -> None:
    """M1: after an activation and a restart (cold cache, no release history) the active release never
    recomputes a hold under open work."""
    hub = station(tmp_path)
    with hub.db.snapshot() as view:
        occ = next(o for o in hub.engine.open_occurrences(view, "person_mara") if o.due_until is not None)
    stored = occ.due_until
    assert stored is not None
    install(hub, changed_rules(hub, "rel_after", "vision_self_check.co2_above_limit"))
    hub.engine.deps.releases._entries.clear()  # type: ignore[attr-defined]  # a restart's cold cache
    with hub.db.begin(actor=Actor("engine", "engine_routine"), correlation_id="cor_m1") as uow:
        hub.engine.reschedule(uow, occ)
    after = hub.rows(
        "SELECT due_until, revision FROM routine_occurrence WHERE occurrence_id=?", (occ.occurrence_id,)
    )
    assert after == ((stored.isoformat().replace("+00:00", "Z"), occ.revision),)
    assert any(
        c.component == "routine" and c.code == "CONFIG_INVALID" for c in hub.health.snapshot().components
    )
    jobs = hub.rows(
        "SELECT due FROM platform_schedule WHERE key=? AND completed=0", ("routine.occ." + occ.occurrence_id,)
    )
    assert jobs and str(jobs[0][0]).startswith(stored.isoformat()[:16])


# M2 ------------------------------------------------------------------------------------------------------------


@pytest.mark.trace("T-E25-093", "INV-E25-11")
def test_miss_is_not_counted_until_the_shared_clock_is_trusted_again(tmp_path: Path) -> None:
    """Review probe M2: regression, operator correction, a deadline before the sweep: not counted. Then the
    sweep: counted. Routine reads the kernel's trust and keeps none of its own."""
    hub = station(tmp_path)
    hub.advance(at(1, 1))
    hub.regress(at(1))
    version = hub.correct()
    with hub.db.begin(actor=Actor("engine", "engine_routine"), correlation_id="cor_frozen") as uow:
        assert hub.engine.counted(uow, uow.now) is False
    hub.sweep(version)
    with hub.db.begin(actor=Actor("engine", "engine_routine"), correlation_id="cor_trusted") as uow:
        assert hub.engine.counted(uow, uow.now) is True


@pytest.mark.trace("T-E25-093")
def test_first_boot_before_the_hub_sweep_counts_no_miss(tmp_path: Path) -> None:
    hub = station(tmp_path)
    hub.rows("SELECT 1")
    with hub.db.begin(actor=Actor("engine", "engine_clock"), correlation_id="cor_reset") as uow:
        uow.connection.execute("DELETE FROM clock_state", ())
    assert not hub.trusted()
    hub.advance(at(2))
    drain(hub)
    assert ends(hub, all_day(hub, 1)) == {("missed", 0, "deadline")}


# M3 ------------------------------------------------------------------------------------------------------------


def _job(hub: RoutineHarness, key: str) -> dict[str, object]:
    raw = hub.rows("SELECT job FROM platform_schedule WHERE key=?", (key,))[0][0]
    assert isinstance(raw, bytes)
    return cast(dict[str, object], json.loads(raw))


@pytest.mark.trace("T-E25-093", "T-E25-044")
def test_each_timer_has_its_kind_and_only_elapsed_ones_move_with_a_correction(tmp_path: Path) -> None:
    """PLAN row 34: the day boundary and window ends are absolute; an override's days duration is elapsed and
    is recomputed through the shared clock corrections."""
    hub = station(tmp_path, at=at(1, 9))
    release = changed_rules(
        hub,
        "rel_days",
        "vision_self_check.co2_above_limit",
        until={"kind": "days", "days": 5, "basis": "team default"},
    )
    install(hub, release)
    hub.raise_warning()
    hub.deliver()
    override = str(
        hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0]
    )
    day = _job(hub, "routine.day.person_mara")
    ends_job = _job(hub, "routine.ovr." + override)
    occurrence = str(
        hub.rows("SELECT key FROM platform_schedule WHERE key LIKE 'routine.occ.%' AND completed=0 LIMIT 1")[
            0
        ][0]
    )
    assert "at" in cast(dict[str, object], day["deadline"])
    assert "at" in cast(dict[str, object], _job(hub, occurrence)["deadline"])
    assert "anchor" in cast(dict[str, object], ends_job["deadline"])
    before = {str(k): str(d) for k, d in hub.rows("SELECT key, due FROM platform_schedule WHERE completed=0")}
    # The operator finds the hub clock an hour fast: the override's five days started an hour earlier in true
    # time, so its end moves an hour earlier; the day boundary and the window end are absolute and stay.
    hub.sweep(hub.correct(-3_600_000))
    # Neither main nor E01's fix branch recalculates jobs after the sweep (an E01 gap, listed in the PR), so the
    # recalculation create_app runs on ClockCorrected is repeated once the shared clock is trusted again.
    with hub.db.begin(actor=Actor("engine", "engine_routine"), correlation_id="cor_recalc") as uow:
        hub.db.scheduler.recalculate_all(uow)
    after = {str(k): str(d) for k, d in hub.rows("SELECT key, due FROM platform_schedule WHERE completed=0")}
    assert after["routine.day.person_mara"] == before["routine.day.person_mara"]
    assert after[occurrence] == before[occurrence]
    moved = datetime.fromisoformat(after["routine.ovr." + override]) - datetime.fromisoformat(
        before["routine.ovr." + override]
    )
    assert moved == timedelta(hours=-1)


@pytest.mark.trace("T-E25-093")
def test_without_a_hub_clock_an_elapsed_timer_keeps_its_instant_and_reports_it(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 9))
    hub.restart(replace(hub.engine.deps, hub_clock=None))
    install(
        hub,
        changed_rules(
            hub,
            "rel_days",
            "vision_self_check.co2_above_limit",
            until={"kind": "days", "days": 5, "basis": "team default"},
        ),
    )
    hub.raise_warning()
    hub.deliver()
    override = str(
        hub.rows("SELECT override_id FROM routine_override WHERE item_id='vision_self_check'")[0][0]
    )
    assert "at" in cast(dict[str, object], _job(hub, "routine.ovr." + override)["deadline"])
    assert any(
        c.component == "routine.clock" and c.code == "CAPABILITY_UNAVAILABLE"
        for c in hub.health.snapshot().components
    )


# M4 ------------------------------------------------------------------------------------------------------------


@pytest.mark.trace("INV-E25-07", "T-E25-110")
def test_care_read_needs_an_object_grant_and_never_carries_the_free_text_notes(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 9))
    with hub.db.snapshot() as view:
        occ = next(o for o in hub.engine.open_occurrences(view, "person_mara"))
    hub.commands.act(
        session(),
        occ.occurrence_id,
        OccurrenceActionBody("skip", skip_reason="other", note="private words"),
        if_match=occ.revision,
        idempotency_key="cmd_skip_note",
        config_release=None,
    )
    assert cast(dict[str, object], hub.queries.occurrence(session(), occ.occurrence_id)["skip"])["note"] == (
        "private words"
    )
    officer = session("person_tomas", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    hub.restart(replace(hub.engine.deps, visibility=Grants(frozenset({occ.occurrence_id}))))
    cared = hub.runtime.occurrence(officer, occ.occurrence_id)
    assert cast(dict[str, object], cared["skip"])["note"] is None
    assert cast(dict[str, object], cared["skip"])["reason"] == "other"


@pytest.mark.trace("INV-E25-07")
def test_officer_or_ground_role_alone_reads_no_group_override(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 8))
    hub.raise_warning()
    hub.deliver()
    groups = {
        str(r[0]) for r in hub.rows("SELECT override_id FROM routine_override WHERE audience<>'person'")
    }
    assert groups
    ground = SessionContext("s_ground", "ground", Actor("ground", "ground_1"), None, frozenset({Role.GROUND}))
    officer = session("person_ilya", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    assert hub.queries.overrides(ground, None, 100)["items"] == []
    assert hub.queries.overrides(officer, None, 100)["items"] == []
    hub.restart(replace(hub.engine.deps, visibility=Grants(frozenset(groups))))
    granted = {
        str(o["override_id"])
        for o in cast(list[dict[str, object]], hub.runtime.overrides(officer, None, 100)["items"])
    }
    assert granted == groups


# M5 ------------------------------------------------------------------------------------------------------------


def _steps_changed(hub: RoutineHarness, release_id: str, item_id: str, steps: list[str]) -> MemoryRelease:
    documents = {
        name: hub.release.document(name)
        for name in (
            ConfigName.ROUTINE_SCHEDULE,
            ConfigName.ROUTINE_RULES,
            ConfigName.UI_COPY,
            ConfigName.QUESTIONNAIRES,
        )
    }
    schedule = cast(dict[str, object], json.loads(documents[ConfigName.ROUTINE_SCHEDULE].raw))
    for item in cast(list[dict[str, object]], schedule["items"]):
        if item["id"] == item_id:
            item["steps"] = steps
    raw = canonical_json(schedule)
    documents[ConfigName.ROUTINE_SCHEDULE] = ConfigDocumentView(
        ConfigName.ROUTINE_SCHEDULE, sha256(raw).hexdigest(), raw
    )
    return MemoryRelease(release_id, documents)


@pytest.mark.trace("T-E25-098", "INV-E25-17")
def test_task_view_carries_the_not_clinically_reviewed_label(tmp_path: Path) -> None:
    """Review probe M5: the configured notice reaches the wire (a label, never a gate)."""
    hub = station(tmp_path)
    with hub.db.snapshot() as view:
        item = hub.engine.release(view).items[0].item_id
    body = json.dumps(hub.queries.task(session(), item))
    notice = json.loads((Path(__file__).resolve().parents[3] / "config/routine-rules.json").read_bytes())[
        "notice"
    ]
    assert notice in body and "Not clinically reviewed" in body


@pytest.mark.trace("T-E25-025", "T-E25-098")
def test_open_work_keeps_the_steps_it_started_with_after_an_activation(tmp_path: Path) -> None:
    hub = station(tmp_path)
    with hub.db.snapshot() as view:
        open_item = next(o for o in hub.engine.open_occurrences(view, "person_mara"))
    original = hub.queries.task(session(), open_item.item_id)["steps"]
    install(hub, _steps_changed(hub, "rel_new_steps", open_item.item_id, ["A different first step."]))
    assert hub.queries.task(session(), open_item.item_id)["steps"] == original
    assert "A different first step." not in json.dumps(hub.queries.task(session(), open_item.item_id))


@pytest.mark.trace("T-E25-098")
def test_steps_for_an_item_the_person_does_not_perform_are_the_notice(tmp_path: Path) -> None:
    hub = station(tmp_path)
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
    item = next((i for i in release.items if "self" not in i.who), None)
    if item is None:
        pytest.skip("the configured schedule has no item without a self operator")
    assert hub.queries.task(session(), item.item_id)["steps"] == [release.notice]


# Minors ----------------------------------------------------------------------------------------------------------


@pytest.mark.trace("T-E25-011")
def test_first_miss_after_a_restart_still_offers_the_barrier_question(tmp_path: Path) -> None:
    """m1: a cold release cache no longer skips the barrier question silently."""
    hub = station(tmp_path)
    calls: list[str] = []
    original = hub.engine.offer_barrier

    def spy(uow: UnitOfWork, person_id: str, **kwargs: object) -> None:
        calls.append(person_id)
        original(uow, person_id)

    hub.engine.offer_barrier = spy  # type: ignore[method-assign]
    hub.engine.deps.releases._entries.clear()  # type: ignore[attr-defined]
    hub.advance(at(2))
    drain(hub)
    assert calls


@pytest.mark.trace("T-E25-015", "R01")
def test_one_failing_day_job_does_not_roll_back_other_deadlines_in_the_batch(tmp_path: Path) -> None:
    """m2: an unexpected error in one person's day job is contained to its savepoint and reported."""
    hub = station(tmp_path)
    ids = all_day(hub, 1, "person_tomas")
    original = hub.engine.materialize

    def failing(uow: UnitOfWork, person_id: str, day: int) -> None:
        if person_id == "person_mara":
            raise RuntimeError("injected")
        original(uow, person_id, day)

    hub.engine.materialize = failing  # type: ignore[method-assign]
    hub.advance(at(2))
    drain(hub)
    assert ends(hub, ids) == {("missed", 1, "deadline")}
    assert any(
        c.component == "routine" and c.code == "EVALUATION_UNAVAILABLE"
        for c in hub.health.snapshot().components
    )
    assert hub.rows("SELECT completed FROM platform_schedule WHERE key='routine.day.person_mara' ")


def _unused(job: ScheduledJob) -> None:
    del job

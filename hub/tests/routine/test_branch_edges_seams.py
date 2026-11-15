"""Seam edges: records evaluation refusals, bounded scans, release subscriptions and daily check notifications."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.records.domain.observations import ObservationRecord
from crew_health.routine.application.evaluator import RoutineEvaluator
from crew_health.routine.application.ports import DailyCheckSubmitted, ReactionTestRecorded
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.application.subscriptions import RoutineSubscriptions
from crew_health.routine.domain.errors import ConfigInvalid
from crew_health.routine.domain.occurrence import Occurrence

from .harness import RoutineHarness, station
from .test_engine import drain, occurrence_for
from .test_integration_inputs import at, audited
from .test_records_seams import evaluation, observation

PRODUCER = Actor("engine", "dailycheck")


def night(tmp_path: Path) -> RoutineHarness:
    hub = station(tmp_path)
    hub.advance(at(0, day=2))
    drain(hub)
    return hub


def reaction_rows(hub: RoutineHarness) -> int:
    count = hub.rows("SELECT COUNT(*) FROM routine_occurrence WHERE item_id='reaction_test' AND periodic=0")[
        0
    ][0]
    assert isinstance(count, int)
    return count


def evaluate(hub: RoutineHarness, record: ObservationRecord, *, records: bool = True) -> None:
    deps = hub.engine.deps if records else replace(hub.engine.deps, records=None)
    evaluator = RoutineEvaluator(deps, hub.engine)
    with hub.db.begin(actor=Actor("person", record.patient_id), correlation_id=hub.ids.new("cor")) as uow:
        hub.records.add_observation(uow, record)
        evaluator.evaluate(uow, evaluation(record))


def test_evaluation_without_a_records_port_reads_nothing(tmp_path: Path) -> None:
    hub = night(tmp_path)
    evaluate(hub, observation(hub), records=False)
    assert reaction_rows(hub) == 0
    evaluate(hub, observation(hub, identifier="obs_sleep_2"))
    assert reaction_rows(hub) == 1


def test_evaluation_of_an_unknown_record_changes_nothing(tmp_path: Path) -> None:
    hub = night(tmp_path)
    evaluator = RoutineEvaluator(hub.engine.deps, hub.engine)
    with hub.db.begin(actor=PRODUCER, correlation_id="cor_unknown") as uow:
        evaluator.evaluate(uow, evaluation(observation(hub, identifier="obs_never_stored")))
    assert reaction_rows(hub) == 0
    assert not audited(hub, "foreign_mission") and not audited(hub, "origin_rejected")


def test_an_observation_from_another_mission_is_audited_and_ignored(tmp_path: Path) -> None:
    hub = night(tmp_path)
    evaluate(hub, replace(observation(hub), mission_id="mission_other"))
    assert reaction_rows(hub) == 0
    assert audited(hub, "foreign_mission")


def test_an_observation_from_a_rejected_origin_is_audited_and_ignored(tmp_path: Path) -> None:
    hub = night(tmp_path)
    assert hub.mission.facts is not None
    hub.mission.facts = replace(hub.mission.facts, mission_mode="LIVE")
    evaluate(hub, replace(observation(hub), origin_mode="REPLAY"))
    assert reaction_rows(hub) == 0
    assert audited(hub, "origin_rejected")


def test_bounded_pages_reads_until_a_short_page_and_refuses_past_its_bound() -> None:
    rows = tuple(range(7))
    calls: list[tuple[int, int]] = []

    def fetch(size: int, offset: int) -> tuple[int, ...]:
        calls.append((size, offset))
        return rows[offset : offset + size]

    assert bounded_pages(fetch, page=3, maximum=10) == rows
    assert calls == [(3, 0), (3, 3), (3, 6)]
    with pytest.raises(ConfigInvalid) as error:
        bounded_pages(fetch, page=3, maximum=5)
    assert error.value.findings[0].pointer == "/routine/capacity"
    calls.clear()
    # A negative bound admits no page at all: nothing is fetched.
    assert bounded_pages(fetch, page=3, maximum=-1) == ()
    assert calls == []


def test_only_a_release_activation_reloads_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = station(tmp_path)
    seen: list[UnitOfWork] = []
    monkeypatch.setattr(hub.engine, "release_activated", lambda uow: seen.append(uow))
    subscriptions = RoutineSubscriptions(hub.engine.deps, hub.engine)
    other = hub.overlay()
    activated = replace(other, type=EventType.CONFIG_RELEASE_ACTIVATED)
    with hub.db.begin(actor=PRODUCER, correlation_id="cor_release") as uow:
        subscriptions.release(uow, other)
        assert seen == []
        subscriptions.release(uow, activated)
        assert seen == [uow]


def carrier(hub: RoutineHarness) -> Occurrence:
    with hub.db.snapshot() as view:
        item = hub.engine.release(view).question_carrier_item_id()
    assert item is not None
    return occurrence_for(hub, item)


def submit(hub: RoutineHarness, person: str, day: int, name: str) -> None:
    with hub.db.begin(actor=PRODUCER, correlation_id=name) as uow:
        uow.publish(DailyCheckSubmitted("dc_" + name, person, day, "SIM", uow.now))


def state_of(hub: RoutineHarness, occ: Occurrence) -> tuple[object, ...]:
    return hub.rows(
        "SELECT state,revision FROM routine_occurrence WHERE occurrence_id=?", (occ.occurrence_id,)
    )[0]


def test_daily_check_submissions_off_roster_or_for_another_day_complete_nothing(tmp_path: Path) -> None:
    hub = station(tmp_path)
    occ = carrier(hub)
    assert occ.due_from is not None
    hub.advance(occ.due_from)
    before = state_of(hub, occ)
    submit(hub, "person_stranger", 1, "stranger")
    submit(hub, "person_mara", 2, "wrong_day")
    assert state_of(hub, occ) == before
    submit(hub, "person_mara", 1, "submitted")
    assert state_of(hub, occ)[0] == "done"
    # No barrier question was pending, so none is recorded as shown.
    assert hub.rows("SELECT COUNT(*) FROM routine_barrier_ask WHERE person_id='person_mara'") == ((0,),)


def test_a_reaction_test_completes_neither_finished_nor_not_yet_due_work(tmp_path: Path) -> None:
    hub = station(tmp_path)
    occ = carrier(hub)
    assert occ.due_from is not None
    hub.advance(occ.due_from)
    submit(hub, "person_mara", 1, "submitted")
    done = state_of(hub, occ)
    later = occurrence_for(hub, "exercise_log")
    assert later.due_from is not None and later.due_from > hub.clock.now()
    pending = state_of(hub, later)
    for target in (occ, later):
        with hub.db.begin(actor=PRODUCER, correlation_id="reaction_" + target.item_id) as uow:
            uow.publish(
                ReactionTestRecorded("rt_" + target.item_id, "person_mara", "completed", target.occurrence_id)
            )
    assert state_of(hub, occ) == done
    assert state_of(hub, later) == pending

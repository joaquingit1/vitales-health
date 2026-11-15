"""Cadence activation edges: refused bindings, capacity, a lost insert, late events, timers and event ends."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import Actor
from crew_health.routine.application.store_port import RoutineStore
from crew_health.routine.domain.bindings import Firing
from crew_health.routine.domain.errors import ConfigInvalid
from crew_health.routine.domain.release import AddOccurrences, Binding, UntilPersonStops

from .harness import RoutineHarness, station
from .test_bounds import changed_rules, install
from .test_integration_inputs import audited

ENGINE = Actor("engine", "engine_routine")
LANDING = "gravity_arrival_check.landing"


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


class LostInsert:
    """The routine store as a concurrent writer leaves it: the named insert finds its row already taken."""

    def __init__(self, store: RoutineStore, method: str) -> None:
        self.store, self.method = store, method

    def __getattr__(self, name: str) -> object:
        if name == self.method:

            def taken(*args: object, **kwargs: object) -> bool:
                return False

            return taken
        return getattr(self.store, name)


def lose(hub: RoutineHarness, method: str) -> None:
    hub.restart(replace(hub.engine.deps, store=cast(RoutineStore, LostInsert(hub.engine.deps.store, method))))


def activations(hub: RoutineHarness) -> tuple[tuple[object, ...], ...]:
    return hub.rows(
        "SELECT activation_id,person_id,state,end_reason,revision FROM routine_cadence_activation ORDER BY activation_id"
    )


def arrival_today(hub: RoutineHarness) -> int:
    count = hub.rows(
        "SELECT COUNT(*) FROM routine_occurrence WHERE person_id='person_mara' AND item_id='gravity_arrival_check'"
    )[0][0]
    assert isinstance(count, int)
    return count


def landing_binding(hub: RoutineHarness) -> Binding:
    with hub.db.snapshot() as view:
        return next(b for b in hub.engine.release(view).bindings if b.binding_id == LANDING)


def start(hub: RoutineHarness, binding: Binding) -> None:
    firing = Firing(binding, "mission_event", "evt_direct", hub.clock.now(), "SIM", (), None)
    with hub.db.begin(actor=ENGINE, correlation_id=hub.ids.new("cor")) as uow:
        release, mission = hub.engine.release(uow), hub.engine.mission(uow)
        hub.engine.activations.start(uow, firing, "person_mara", mission, release)


def test_only_a_replace_cadence_binding_with_a_supported_end_starts_an_activation(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1))
    binding = landing_binding(hub)
    start(hub, replace(binding, effect=AddOccurrences(True, (), None, False, "team default")))
    assert activations(hub) == ()
    with pytest.raises(ConfigInvalid) as error:
        start(hub, replace(binding, until=UntilPersonStops("crew_medical_officer")))
    assert error.value.findings[0].pointer == "/until"
    assert activations(hub) == ()
    start(hub, binding)
    assert len(activations(hub)) == 1


def test_a_landing_beyond_the_activation_bound_is_refused_as_capacity(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1))
    hub.mission_event()
    hub.deliver()
    first = activations(hub)
    assert len(first) == 4
    deps = hub.engine.deps
    hub.restart(replace(deps, settings=replace(deps.settings, max_bindings=1)))
    hub.mission_event(event_id="evt_landing_2")
    hub.deliver()
    assert activations(hub) == first
    assert audited(hub, "capacity")


def test_an_activation_lost_to_a_concurrent_insert_schedules_and_adds_nothing(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1))
    before = arrival_today(hub)
    lose(hub, "insert_activation")
    hub.mission_event()
    hub.deliver()
    assert activations(hub) == ()
    assert arrival_today(hub) == before
    assert hub.rows("SELECT COUNT(*) FROM platform_schedule WHERE key LIKE 'routine.rca.%'") == ((0,),)


def test_a_landing_whose_tightening_already_elapsed_adds_no_work_today(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(8))
    before = arrival_today(hub)
    hub.mission_event(event_id="evt_landing_late", occurred_at=at(1))
    hub.deliver()
    assert len(activations(hub)) == 4
    assert arrival_today(hub) == before
    hub.mission_event(event_id="evt_landing_now")
    hub.deliver()
    assert arrival_today(hub) == before + 1


def test_activation_timers_ignore_unknown_activations_and_wait_for_their_end(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1))
    hub.mission_event()
    hub.deliver()
    identifier = str(activations(hub)[0][0])
    key = "routine.rca." + identifier
    with hub.db.begin(actor=ENGINE, correlation_id="cor_rca") as uow:
        hub.engine.activations.job(
            uow,
            ScheduledJob(
                key, "routine.activation", AbsoluteDeadline(uow.now), {"activation_id": "rca_missing"}, None
            ),
        )
        generation = hub.engine.deps.store.deadline_generation(uow, key)
        hub.engine.activations.job(
            uow,
            ScheduledJob(
                key, "routine.activation", AbsoluteDeadline(uow.now), {"activation_id": identifier}, None
            ),
        )
        later = hub.engine.deps.store.deadline_generation(uow, key)
    assert generation is not None and later is not None and later > generation
    assert all(row[2] == "active" for row in activations(hub))


def test_an_activation_ends_only_on_the_mission_event_its_binding_names(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1))
    install(
        hub,
        changed_rules(
            hub, "rel_until_launch", LANDING, until={"kind": "mission_event", "event_types": ["launch"]}
        ),
    )
    hub.mission_event()
    hub.deliver()
    assert len(activations(hub)) == 4
    hub.mission_event("space_weather_alert", event_id="evt_weather")
    hub.deliver()
    assert all(row[2] == "active" for row in activations(hub))
    hub.mission_event("launch", event_id="evt_launch")
    hub.deliver()
    assert {(row[2], row[3]) for row in activations(hub)} == {("ended", "mission_event")}

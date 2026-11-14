"""E23 section 12 tests T-E23-015, 016, 044, 067, 088 (mission events and state facts, mission half)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from e23m_helpers import START, Station, build_station, documents, edited, ground, operator, person
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ConfigName, EventType
from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.queries import event_model
from crew_health.mission.application.use_cases import Caller, EventTimeFacts, create_overlay, record_event
from crew_health.mission.domain import events as rules
from crew_health.mission.domain import state_facts as facts
from crew_health.mission.domain.errors import InvalidValue, OriginMismatch, UnauthorizedScope

pytestmark = pytest.mark.e23
EXAMPLES = settings(max_examples=1000, deadline=None, suppress_health_check=list(HealthCheck))


def crew(person_id: str = "per_a") -> Caller:
    return person(person_id, "crew")


def init(station: Station, environment: str) -> None:
    with station.run(operator().actor) as uow:
        initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="node",
            environment_id=environment,
        )


def record(
    station: Station,
    event_type: str,
    at_minutes: int,
    *,
    who: Caller | None = None,
    persons: tuple[str, ...] = (),
    origin: str = "SIM",
    note: str | None = None,
    state: str = "known",
):  # type: ignore[no-untyped-def]
    caller = who or crew()
    at = START + timedelta(minutes=at_minutes)
    with station.run(caller.actor) as uow:
        return record_event(
            station.services,
            uow,
            caller,
            event_type=event_type,
            occurred_at=at,
            person_ids=persons,
            location=None,
            note=note,
            origin_mode=origin,  # type: ignore[arg-type]
            time=EventTimeFacts(state, at if state in ("known", "replay") else None),  # type: ignore[arg-type]
        )


@pytest.mark.trace("T-E23-015", "C12", "3.6")
def test_the_checks_run_in_order_type_role_origin_persons(station: Station) -> None:
    init(station, "mars_surface")
    with pytest.raises(InvalidValue):
        record(station, "no_such_event", 1, who=station_caller(), origin="LIVE")  # the type is checked first
    with pytest.raises(UnauthorizedScope):
        record(
            station, "landing", 1, who=crew(), origin="LIVE"
        )  # crew may not record a landing; origin never reached
    with pytest.raises(OriginMismatch):
        record(station, "landing", 1, who=ground(), origin="LIVE")  # the mission is SIM
    with pytest.raises(InvalidValue):
        record(station, "eva_planned", 1, persons=("per_zzz",))  # not on the roster
    with pytest.raises(InvalidValue):
        record(station, "eva_planned", 1, persons=("per_a", "per_a"))
    assert station.store.event_rows == []  # a refused event leaves nothing behind
    stored = record(station, "landing", 2, who=ground())
    assert (stored.environment_id, stored.recorded_by_kind, stored.recorded_by_id) == (
        "mars_surface",
        "ground",
        "gnd_1",
    )


def station_caller() -> Caller:
    from e23m_helpers import station_session

    return station_session()


@pytest.mark.trace("T-E23-015", "C12", "3.6")
def test_a_state_fact_changes_only_by_its_listed_event_types_in_its_listed_environments(
    station: Station,
) -> None:
    init(station, "mars_surface")
    record(station, "eva_planned", 1)
    assert station.store.fact_rows == {}  # an unrelated event type changes nothing
    record(station, "local_water_source_added", 2)
    assert station.store.fact_rows["local_water_untested"].value is True
    record(station, "local_water_tested", 5)
    assert station.store.fact_rows["local_water_untested"].value is False
    record(station, "local_water_source_added", 3)  # delayed: older than the test that cleared it
    assert station.store.fact_rows["local_water_untested"].value is False
    record(station, "local_water_source_added", 6)
    assert station.store.fact_rows["local_water_untested"].value is True
    unknown = record(station, "local_water_tested", 9, state="unknown")  # no trusted time: kept, no effect
    assert station.store.fact_rows["local_water_untested"].value is True
    assert unknown.event_id in {e.event_id for e in station.store.event_rows}


@pytest.mark.trace("T-E23-016", "C12")
def test_a_state_fact_is_untouched_in_an_environment_not_listed(station: Station) -> None:
    init(station, "lunar_surface")
    record(station, "local_water_source_added", 2)
    assert station.store.fact_rows == {}
    assert len(station.store.event_rows) == 1


@pytest.mark.trace("T-E23-015", "T-E23-009", "C12")
@given(
    sequence=st.lists(
        st.tuples(
            st.sampled_from(["set", "clear", "other"]),
            st.integers(0, 50),
            st.sampled_from(["mars_surface", "transit"]),
        ),
        max_size=25,
    )
)
@EXAMPLES
def test_fact_state_follows_the_latest_listed_event_whatever_the_arrival_order(
    sequence: list[tuple[str, int, str]],
) -> None:
    config = (facts.StateFactConfig("f", "F", ("set_it",), ("clear_it",), ("mars_surface",)),)
    names = {"set": "set_it", "clear": "clear_it", "other": "other"}
    state: dict[str, facts.FactState] = {}
    applied: list[tuple[int, str, bool]] = []
    for number, (kind, minute, environment) in enumerate(sequence):
        at = START + timedelta(minutes=minute)
        for changed in facts.apply_event(
            config,
            state,
            event_type=names[kind],
            environment_id=environment,
            at=at,
            event_id=f"e{number:03d}",
            origin_mode="SIM",
        ):
            state[changed.fact] = changed
        if kind != "other" and environment == "mars_surface":
            applied.append((minute, f"e{number:03d}", kind == "set"))
    if applied:
        expected = max(applied, key=lambda item: (item[0], item[1]))[2]  # latest by time, event_id on ties
        assert state["f"].value is expected
    else:
        assert state == {}


@pytest.mark.trace("T-E23-044", "V58", "UC05")
def test_shelter_attendance_is_stored_with_person_ids_and_served(station: Station) -> None:
    raw = edited(
        documents(),
        ConfigName.ENVIRONMENT_PROFILES,
        lambda d: d["mission_event_types"].append(
            {
                "id": "shelter_attended",
                "label": "Shelter attended",
                "starts_overlay": None,
                "ends_overlay": None,
                "who_may_record": ["crew", "operator"],
            }
        ),
    )
    scratch = build_station(raw)  # the package example has no shelter vocabulary: a scratch release adds one
    init(scratch, "mars_surface")
    stored = record(scratch, "shelter_attended", 3, persons=("per_a", "per_b"))
    assert stored.person_ids == ("per_a", "per_b")
    served = event_model(stored)
    assert [p.root for p in served.person_ids] == [
        "per_a",
        "per_b",
    ] and served.event_type.root == "shelter_attended"
    assert (
        scratch.store.overlay_rows == {} and scratch.store.fact_rows == {}
    )  # decided from nothing: stored and served only
    assert "shelter_attended" not in {
        t.id for t in station.config.event_types
    }  # absent from the example release


@pytest.mark.trace("T-E23-088", "D2", "V57", "V58")
def test_a_storm_event_during_sleep_is_recorded_and_forwarded_without_downgrading_anything(
    station: Station,
) -> None:
    init(station, "lunar_surface")
    with station.run(crew().actor) as uow:
        sleeper = create_overlay(
            station.services, uow, crew(), kind="sleep", planned_start=None, started_at=START, location=None
        )
    before = len(station.factory.state.events)
    storm = record(station, "space_weather_alert", 30, who=ground())
    assert (
        station.store.overlay_rows[sleeper.overlay_id] == sleeper
    )  # sleep is untouched: no suppression here
    forwarded = station.factory.state.events[before:]
    assert [e.type for e in forwarded] == [EventType.MISSION_EVENT_RECORDED] * 2  # crew and operator scopes
    assert json.loads(forwarded[0].data_json)["event_type"] == "space_weather_alert"
    assert json.loads(forwarded[0].data_json)["event_id"] == storm.event_id
    assert station.state.current() is not None  # no model and no ground provider is needed for any of it


@pytest.mark.trace("T-E23-067", "3.6", "R-E23-5")
def test_untrusted_time_events_are_retained_for_recovery_and_change_no_state(station: Station) -> None:
    init(station, "mars_surface")
    for state in ("unknown", "future"):
        record(station, "local_water_source_added", 4, state=state)
        record(station, "sleep_started", 4, persons=("per_a",), state=state)
    assert station.store.fact_rows == {} and station.store.overlay_rows == {}
    assert len(station.store.event_rows) == 4
    assert all(not e.orders_state for e in station.store.event_rows)


@pytest.mark.trace("T-E23-015", "3.6")
def test_check_record_requires_a_role_among_who_may_record() -> None:
    types = (rules.EventTypeFacts("x", "X", None, None, ("operator",)),)
    kwargs = {"types": types, "mission_mode": "SIM", "body_origin": "SIM", "person_ids": (), "roster": ()}
    assert rules.check_record(event_type="x", roles={"operator", "crew"}, **kwargs).id == "x"  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedScope):
        rules.check_record(event_type="x", roles=set(), **kwargs)  # type: ignore[arg-type]

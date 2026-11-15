"""Cadence activation lifetime, event scoping and observation boundary properties."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.uow import Actor
from crew_health.platform.domain.errors import StorageConstraintViolated
from crew_health.routine.domain.bindings import (
    MissionEventInput,
    ObservationInput,
    match_bindings,
    observation_matches,
)
from crew_health.routine.domain.loader_checks import Bounds, check_release
from crew_health.routine.domain.release import ObservationOn, UntilEffectComplete

from .harness import RoutineHarness, session, station
from .support import NOW, release
from .test_bounds import changed_rules, install
from .test_integration_inputs import at, drain_timers


def land(hub: RoutineHarness) -> None:
    assert hub.mission.facts is not None
    hub.mission.facts = replace(hub.mission.facts, active_environment_id="lunar_surface")
    hub.mission_event()
    hub.deliver()


@pytest.mark.trace("T-E25-031", "T-E25-044", "D18")
def test_sim_landing_activates_every_member_for_exactly_five_mission_days(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(0))
    land(hub)
    with hub.db.snapshot() as view:
        activations = hub.engine.activations.active(view)
        item = hub.engine.release(view).item("gravity_arrival_check")
    assert item is not None
    assert {a.person_id for a in activations} == hub.roster.person_ids()
    assert all(a.cadence.word == "daily" and a.source_origin_mode == "SIM" for a in activations)
    assert all(a.ends_at == at(0, day=6) for a in activations)
    assert all(a.source_kind == "mission_event" and a.source_ref == "evt_landing_1" for a in activations)
    before = hub.rows(
        "SELECT activation_id,revision,state FROM routine_cadence_activation ORDER BY activation_id"
    )
    hub.mission_event()
    hub.deliver()
    assert (
        hub.rows("SELECT activation_id,revision,state FROM routine_cadence_activation ORDER BY activation_id")
        == before
    )
    for day in range(1, 6):
        hub.advance(at(0, day=day))
        drain_timers(hub)
        for member in hub.roster.roster:
            task = hub.queries.task(session(member.person_id), item.item_id)
            assert cast(dict[str, object], task["effective_cadence"])["word"] == "daily"
            assert task["basis"] == item.cadence_basis
            assert cast(dict[str, object], task["citation"])["source_ids"] == list(item.source_ids)
            assert task["active_override_ids"] == []
    hub.advance(at(0, day=6))
    drain_timers(hub)
    assert hub.rows("SELECT DISTINCT state,end_reason FROM routine_cadence_activation") == (
        ("ended", "days_elapsed"),
    )
    for member in hub.roster.roster:
        task = hub.queries.task(session(member.person_id), item.item_id)
        assert task["effective_cadence"] == task["base_cadence"]
        assert hub.rows(
            "SELECT mission_day FROM routine_occurrence WHERE item_id=? AND person_id=? ORDER BY mission_day",
            (item.item_id, member.person_id),
        ) == ((1,), (2,), (3,), (4,), (5,))


@pytest.mark.trace("T-E25-031", "T-E25-044", "D18")
def test_environment_scoped_event_requires_both_active_and_source_environment() -> None:
    parsed = release()
    landing = next(b for b in parsed.bindings if b.binding_id == "gravity_arrival_check.landing")
    scoped = replace(parsed, bindings=(replace(landing, environment_id="lunar_surface"),))
    event = MissionEventInput("evt_landing", "landing", NOW, "SIM", "lunar_surface", (), None)
    assert len(match_bindings(scoped, event, "lunar_surface")) == 1
    assert match_bindings(scoped, replace(event, environment_id="mars_surface"), "lunar_surface") == ()
    assert match_bindings(scoped, event, "mars_surface") == ()


@pytest.mark.trace("T-E25-031", "T-E25-044")
def test_activation_end_waits_for_a_trusted_clock(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(0))
    land(hub)
    hub.advance(at(0, day=6) + timedelta(minutes=1))
    hub.regress(at(0, day=6))
    drain_timers(hub)
    assert hub.rows("SELECT DISTINCT state FROM routine_cadence_activation") == (("active",),)
    hub.confirm_time()
    hub.advance(hub.clock.now() + timedelta(milliseconds=hub.engine.deps.settings.miss_grace_ms))
    drain_timers(hub)
    assert hub.rows("SELECT DISTINCT state,end_reason FROM routine_cadence_activation") == (
        ("ended", "days_elapsed"),
    )


@pytest.mark.trace("T-E25-031", "T-E25-044", "INV-E25-01")
def test_ending_activation_preserves_its_open_pinned_occurrence_and_finality(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(0))
    land(hub)
    before = hub.rows(
        "SELECT occurrence_id,config_release_id,due_until,state FROM routine_occurrence WHERE item_id='gravity_arrival_check' AND person_id='person_mara'"
    )
    assert len(before) == 1
    with hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_rca_end") as uow:
        activation = hub.engine.activations.active(uow, "person_mara")[0]
        hub.engine.activations.end(uow, activation, "mission_event")
    assert (
        hub.rows(
            "SELECT occurrence_id,config_release_id,due_until,state FROM routine_occurrence WHERE item_id='gravity_arrival_check' AND person_id='person_mara'"
        )
        == before
    )
    for statement in (
        "UPDATE routine_cadence_activation SET revision=revision+1 WHERE activation_id=?",
        "DELETE FROM routine_cadence_activation WHERE activation_id=?",
    ):
        with (
            pytest.raises(StorageConstraintViolated),
            hub.db.begin(actor=Actor("engine", "routine"), correlation_id="cor_rca_immutable") as uow,
        ):
            uow.connection.execute(statement, (activation.activation_id,))
    hub.advance(at(0, day=2))
    drain_timers(hub)
    assert hub.rows("SELECT state FROM routine_occurrence WHERE occurrence_id=?", (before[0][0],)) == (
        ("missed",),
    )


@pytest.mark.trace("T-E25-036", "D17")
@pytest.mark.parametrize("comparator", ("above", "at_or_above", "below", "at_or_below"))
@given(threshold=st.integers(-100000, 100000), step=st.integers(1, 10000))
def test_observation_comparators_preserve_strict_and_inclusive_boundaries(
    comparator: Literal["above", "at_or_above", "below", "at_or_below"], threshold: int, step: int
) -> None:
    configured = next(b.on for b in release().bindings if isinstance(b.on, ObservationOn))
    value = Decimal(threshold) / Decimal(1000)
    delta = Decimal(step) / Decimal(1000)
    on = replace(configured, comparator=comparator, value=value)
    inp = ObservationInput("obs_property", "person_mara", on.code, value, on.unit, NOW, "SIM", "accepted")
    expected = {
        "above": (False, False, True),
        "at_or_above": (False, True, True),
        "below": (True, False, False),
        "at_or_below": (True, True, False),
    }[comparator]
    assert (
        tuple(observation_matches(on, replace(inp, value=x)) for x in (value - delta, value, value + delta))
        == expected
    )
    assert not observation_matches(on, replace(inp, quality_status="rejected"))
    assert not observation_matches(on, replace(inp, unit=on.unit + "_other"))
    assert not observation_matches(on, replace(inp, code=on.code + "_other"))


@pytest.mark.trace("T-E25-031", "T-E25-044")
def test_activation_mission_event_end_uses_the_pinned_binding(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(0))
    pinned = changed_rules(
        hub,
        "rel_rca_event",
        "gravity_arrival_check.landing",
        until={"kind": "mission_event", "event_types": ["exercise_ended"]},
    )
    install(hub, pinned)
    land(hub)
    install(
        hub,
        changed_rules(
            hub,
            "rel_rca_changed",
            "gravity_arrival_check.landing",
            until={"kind": "mission_event", "event_types": ["landing"]},
        ),
    )
    hub.mission_event("exercise_ended", event_id="evt_rca_end")
    hub.deliver()
    assert hub.rows("SELECT DISTINCT state,end_reason,config_release_id FROM routine_cadence_activation") == (
        ("ended", "mission_event", pinned.release_id),
    )


@pytest.mark.trace("T-E25-031", "T-E25-044")
def test_warning_activation_repoints_without_duplicates_and_ends_on_successor_clear(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(0))
    install(
        hub,
        changed_rules(
            hub,
            "rel_rca_warning",
            "gravity_arrival_check.landing",
            on={"kind": "warning_raised", "definition_ids": ["co2_above_limit"]},
            until={"kind": "warning_cleared"},
        ),
    )
    hub.raise_warning()
    hub.deliver()
    before = hub.rows("SELECT activation_id FROM routine_cadence_activation ORDER BY activation_id")
    assert len(before) == len(hub.roster.roster)
    hub.clear_warning(reason="superseded")
    hub.raise_warning("wrn_co2_2", previous_warning_id="wrn_co2_1")
    hub.deliver()
    assert hub.rows("SELECT activation_id FROM routine_cadence_activation ORDER BY activation_id") == before
    assert hub.rows("SELECT DISTINCT source_ref,state FROM routine_cadence_activation") == (
        ("wrn_co2_2", "active"),
    )
    hub.clear_warning("wrn_co2_1")
    hub.deliver()
    assert hub.rows("SELECT DISTINCT state FROM routine_cadence_activation") == (("active",),)
    hub.clear_warning("wrn_co2_2")
    hub.deliver()
    assert hub.rows("SELECT DISTINCT state,end_reason FROM routine_cadence_activation") == (
        ("ended", "warning_cleared"),
    )


@pytest.mark.trace("T-E25-031", "T-E25-044")
def test_effect_complete_is_a_loader_error_for_cadence_activation() -> None:
    parsed = release()
    binding = next(b for b in parsed.bindings if b.binding_id == "gravity_arrival_check.landing")
    invalid = replace(parsed, bindings=(replace(binding, until=UntilEffectComplete()),))
    findings = check_release(invalid, Bounds(100, 100, 100), None, None)
    assert any(f.severity == "error" and f.pointer == "/bindings/0/until" for f in findings)

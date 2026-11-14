"""E23 section 12 tests T-E23-001, 002, 004 to 010, 046, 064, 068, 069 (profile state machine, on kernel fakes)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from e23m_helpers import START, Station, build_station, ground, operator, person, station_session
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ConfigName, EventType
from crew_health.kernel.scheduler import AbsoluteDeadline
from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.use_cases import (
    ACTIVATE_HANDLER,
    Caller,
    act_on_change,
    activate_due,
    propose_change,
)
from crew_health.mission.domain import day as day_rules
from crew_health.mission.domain import profile as rules
from crew_health.mission.domain.errors import (
    ConfirmationRequired,
    InvalidTransition,
    InvalidValue,
    MissionDomainError,
    NotInitialized,
    StaleVersion,
    UnauthorizedScope,
)

pytestmark = pytest.mark.e23
EXAMPLES = settings(max_examples=1000, deadline=None, suppress_health_check=list(HealthCheck))
CREW = frozenset({"crew"})


def start(station: Station, mode: str = "SIM", environment: str = "station_leo") -> rules.ProfileRecord:
    with station.run(operator().actor) as uow:
        return initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode=mode,  # type: ignore[arg-type]
            planned_length_days=30,
            site_node_id="node",
            environment_id=environment,
        )


def propose(
    station: Station, environment: str = "lunar_surface", *, reason: str = "operator", effective_at=None
) -> rules.ProfileRecord:  # type: ignore[no-untyped-def]
    caller = operator()
    with station.run(caller.actor) as uow:
        return propose_change(
            station.services,
            uow,
            caller,
            environment_id=environment,
            reason=reason,  # type: ignore[arg-type]
            effective_at=effective_at,
        )


def confirm(
    station: Station, who: Caller, record: rules.ProfileRecord, action: str = "confirm"
) -> rules.ProfileRecord:
    with station.run(who.actor) as uow:
        return act_on_change(
            station.services,
            uow,
            who,
            profile_id=record.profile_id,
            action=action,  # type: ignore[arg-type]
            expected_revision=record.revision,
        )


def active_of(station: Station) -> rules.ProfileRecord:
    return next(r for r in station.store.profiles(None) if r.state == "active")


def crew(person_id: str = "per_a") -> Caller:
    return person(person_id, "crew")


@pytest.mark.trace("T-E23-073", "3.3", "R-E23-4")
def test_mission_init_creates_one_identity_and_the_first_profile_and_refuses_a_second(
    station: Station,
) -> None:
    record = start(station)
    assert (record.state, record.reason, record.confirmations) == ("active", "mission_start", ())
    assert record.activated_at == START == record.environment_day_start == record.effective_at
    assert station.store.identity_row is not None and station.store.identity_row.mission_mode == "SIM"
    assert station.spy.calls == [("station_leo", 3)]  # the participant ran after the outbox event
    with pytest.raises(InvalidTransition):
        start(station)
    assert len(station.store.profile_rows) == 1  # the refused init left nothing behind
    with pytest.raises(UnauthorizedScope):
        with station.run(crew().actor) as uow:
            initialize_mission(
                station.services,
                uow,
                crew(),
                mission_mode="SIM",
                planned_length_days=None,
                site_node_id="node",
                environment_id="station_leo",
            )


@pytest.mark.trace("T-E23-001", "R34", "V66", "D18")
def test_a_proposal_changes_nothing_until_confirmed(station: Station) -> None:
    before = start(station)
    proposal = propose(station)
    assert (
        proposal.state == "proposed"
        and proposal.reason == "operator"
        and proposal.confirmations_required == 1
    )
    assert active_of(station).profile_id == before.profile_id  # the station is still the active environment
    assert station.state.active_environment_id() == "station_leo"
    assert [e.type for e in station.factory.state.events][-1] == EventType.ENVIRONMENT_CHANGE_PROPOSED
    assert proposal.lists.sensor_type_ids  # the display lists are filled at proposal


@pytest.mark.trace("T-E23-002", "R34", "D18", "D33")
def test_confirmation_activates_in_one_unit_of_work_in_the_c12_order(station: Station) -> None:
    old = start(station)
    proposal = propose(station)
    station.advance(minutes=5)
    activated = confirm(station, crew(), proposal)
    assert (
        activated.state == "active"
        and activated.environment_day_start == activated.activated_at == station.clock.at
    )
    assert activated.effective_at == station.clock.at  # null effective_at is set to the activation instant
    superseded = station.store.profile_rows[old.profile_id]
    assert superseded.state == "superseded" and superseded.superseded_at == station.clock.at
    types = [e.type for e in station.factory.state.events]
    assert types[-3:] == [EventType.ENVIRONMENT_ACTIVATED] * 3  # crew, ground and operator scopes
    assert station.spy.calls[-1] == (
        "lunar_surface",
        len(station.factory.state.events),
    )  # after the outbox event
    assert activated.lists.limit_ids and "dust_lunar_act" in activated.lists.limit_ids
    assert activated.config_release_id == "rel_test_0001"
    assert activated.ground_delay_text.startswith("Seconds. NASA gives 6 to 10 seconds")


@pytest.mark.trace("T-E23-002", "3.3", "AGENTS6")
def test_a_failure_anywhere_in_activation_leaves_the_old_environment_untouched(station: Station) -> None:
    start(station)
    proposal = propose(station)
    station.spy.fail = True
    events_before = len(station.factory.state.events)
    with pytest.raises(RuntimeError):
        confirm(station, crew(), proposal)
    assert active_of(station).environment_id == "station_leo"
    assert station.store.profile_rows[proposal.profile_id].state == "proposed"
    assert len(station.factory.state.events) == events_before  # the outbox event rolled back with it
    station.spy.fail = False
    assert confirm(station, crew(), proposal).state == "active"  # a retry succeeds with one effect


@pytest.mark.trace("T-E23-004", "I-M2")
def test_a_second_open_proposal_is_invalid_transition(station: Station) -> None:
    start(station)
    propose(station)
    with pytest.raises(InvalidTransition):
        propose(station, "mars_surface")


@pytest.mark.trace("T-E23-005", "I-M1", "S5.8")
def test_two_concurrent_confirmations_one_activation_one_stale_version(station: Station) -> None:
    start(station)
    proposal = propose(station)
    first = confirm(station, crew("per_a"), proposal)
    assert first.state == "active"
    with pytest.raises(StaleVersion):
        confirm(station, crew("per_b"), proposal)  # the second client held the old revision
    with pytest.raises(InvalidTransition):
        confirm(station, crew("per_b"), first)  # at the new revision the change is no longer open
    assert len([r for r in station.store.profile_rows.values() if r.state == "active"]) == 1


@pytest.mark.trace("T-E23-006", "I-M6", "R34")
def test_any_crew_session_may_confirm_or_reject_a_station_session_may_not(station: Station) -> None:
    start(station)
    proposal = propose(station)
    for outsider in (station_session(), ground(), operator()):
        with pytest.raises(UnauthorizedScope):
            confirm(station, outsider, proposal)
    with pytest.raises(UnauthorizedScope):
        confirm(station, person("per_a", "crew_medical_officer"), proposal)  # a person without the crew role
    rejected = confirm(station, crew("per_c"), proposal, "reject")
    assert (
        rejected.state == "rejected"
        and rejected.rejected_by is not None
        and rejected.rejected_by.id == "per_c"
    )
    assert active_of(station).environment_id == "station_leo"
    assert [e.type for e in station.factory.state.events][-1] == EventType.ENVIRONMENT_CHANGE_REJECTED


@pytest.mark.trace("T-E23-007", "I-M4")
def test_demo_switch_is_refused_in_a_live_mission_and_allowed_in_sim_and_replay() -> None:
    live = build_station()
    start(live, "LIVE")
    with pytest.raises(InvalidValue):
        propose(live, reason="demo_switch")
    for mode in ("SIM", "REPLAY"):
        demo = build_station()
        start(demo, mode)
        assert propose(demo, reason="demo_switch").reason == "demo_switch"


@pytest.mark.trace("T-E23-004", "I-M5")
def test_proposing_the_active_environment_again_is_invalid_value(station: Station) -> None:
    start(station)
    with pytest.raises(InvalidValue):
        propose(station, "station_leo")
    with pytest.raises(InvalidValue):
        propose(station, "mission_control")  # an environment with no profile


@pytest.mark.trace("T-E23-008", "3.1", "I-M3")
def test_effective_at_later_than_confirmation_activates_at_that_instant_and_is_absolute(
    station: Station,
) -> None:
    start(station)
    effective = START + timedelta(hours=2)
    proposal = propose(station, effective_at=effective)
    waiting = confirm(station, crew(), proposal)
    assert waiting.state == "proposed" and len(waiting.confirmations) == 1  # counted, not yet active
    job, generation, _ = station.factory.state.schedules["mission.activate." + proposal.profile_id]
    assert job.handler == ACTIVATE_HANDLER and job.deadline == AbsoluteDeadline(effective)
    station.advance(minutes=119)
    with station.run(operator().actor) as uow:
        activate_due(station.services, uow, job)
    assert station.store.profile_rows[proposal.profile_id].state == "proposed"  # too early: nothing
    station.advance(minutes=1, seconds=1)
    with station.run(operator().actor) as uow:
        activate_due(station.services, uow, job)
    done = station.store.profile_rows[proposal.profile_id]
    assert done.state == "active" and done.activated_at == station.clock.at and done.effective_at == effective
    with station.run(operator().actor) as uow:
        activate_due(station.services, uow, job)  # a repeated job is a no-op
    assert (
        generation == 1 and len([r for r in station.store.profile_rows.values() if r.state == "active"]) == 1
    )


@pytest.mark.trace("T-E23-009", "I-M1", "I-M2", "I-M3")
@given(
    operations=st.lists(
        st.one_of(
            st.tuples(st.just("propose"), st.integers(0, 3)),
            st.tuples(st.just("confirm"), st.integers(0, 2)),
            st.tuples(st.just("reject"), st.integers(0, 2)),
            st.tuples(st.just("tick"), st.integers(0, 3)),
        ),
        max_size=25,
    ),
    required=st.integers(1, 3),
)
@EXAMPLES
def test_any_sequence_of_propose_confirm_reject_keeps_exactly_one_active_record(
    operations: list[tuple[str, int]], required: int
) -> None:
    template = rules.ProfileTemplate("station_leo", "L", "p", "w", "d", 1)
    lists = rules.ProfileLists((), (), (), (), (), ())
    now = START
    people = ("per_a", "per_b", "per_c")
    environments = ("station_leo", "transit", "lunar_surface", "mars_surface")
    records = [
        rules.start_profile(
            profile_id="env_0",
            template=template,
            actor=rules.ActorRef("operator", "o"),
            now=now,
            confirmations_required=required,
            release_id="rel",
            lists=lists,
        )
    ]
    for number, (name, argument) in enumerate(operations, start=1):
        proposal = next((r for r in records if r.state == "proposed"), None)
        active = next(r for r in records if r.state == "active")
        try:
            if name == "propose":
                tpl = replace(template, environment_id=environments[argument])
                records.append(
                    rules.propose(
                        profile_id=f"env_{number}",
                        template=tpl,
                        reason="operator",
                        effective_at=None,
                        proposed_by=rules.ActorRef("operator", "o"),
                        now=now,
                        mission_mode="SIM",
                        active=active,
                        open_proposal=proposal,
                        confirmations_required=required,
                        release_id="rel",
                        lists=lists,
                    )
                )
            elif name == "tick":
                now = now + timedelta(minutes=argument)
            elif proposal is not None:
                updated = (
                    rules.confirm(proposal, people[argument], now)
                    if name == "confirm"
                    else rules.reject(proposal, rules.ActorRef("person", people[argument]))
                )
                records[records.index(proposal)] = updated
                if rules.due(updated, now):
                    old, new = rules.activate(active, updated, now=now, release_id="rel", lists=lists)
                    records[records.index(active)], records[records.index(updated)] = old, new
        except MissionDomainError:
            pass
        rules.assert_single_active(records)  # I-M1 and I-M2 hold in every committed state
        for r in records:
            assert len({c.person_id for c in r.confirmations}) == len(r.confirmations)  # a person counts once
            if r.state == "active" and r.reason != "mission_start":
                assert len(r.confirmations) >= r.confirmations_required


@pytest.mark.trace("T-E23-010", "C12", "S5.3")
def test_day_label_vectors_and_the_historical_inline_label_after_a_switch(station: Station) -> None:
    # CONTRACTS 12: environment day = floor((t - start) / day length) + 1; short Day 19 / Sol 3; inline day 21 / sol 7
    begin = START
    assert day_rules.environment_day(begin, begin, 86400) == 1
    assert day_rules.environment_day(begin + timedelta(seconds=86399), begin, 86400) == 1
    assert day_rules.environment_day(begin + timedelta(days=2, seconds=1), begin, 86400) == 3
    assert day_rules.environment_day(begin - timedelta(seconds=1), begin, 86400) is None
    assert day_rules.render("sol {day} on Mars", day=7, total=None, unknown="?") == "sol 7 on Mars"
    assert (
        day_rules.render("flight day {day} of {total}", day=3, total=None, unknown="unknown")
        == "flight day 3 of unknown"
    )
    with pytest.raises(InvalidValue):
        day_rules.render("{nope}", day=1, total=1, unknown="?")
    start(station, environment="transit")
    inline_transit = station.state.inline_label_at(START + timedelta(days=1), unknown="unknown")
    proposal = propose(station, "mars_surface")
    station.advance(days=3)
    confirm(station, crew(), proposal)
    assert station.state.inline_label_at(START + timedelta(days=1), unknown="unknown") == inline_transit
    after = station.state.inline_label_at(station.clock.at + timedelta(days=6), unknown="unknown")
    assert after == "sol 7"  # the Mars profile's own words and counter for an instant after the switch


@pytest.mark.trace("T-E23-046", "C12")
def test_return_option_is_served_unchanged_and_none_where_null(station: Station) -> None:
    from crew_health.mission.application.ports import ReturnOption

    leo = station.config.profile("station_leo")
    mars = station.config.profile("mars_surface")
    assert leo is not None and mars is not None and leo.return_option is not None
    assert leo.return_option.trip_text == "Return from the station: to be set by flight rules."
    assert leo.return_option.to_be_set_by == "flight rules" and mars.return_option is None
    options = {
        p.template.environment_id: None
        if p.return_option is None
        else ReturnOption(p.return_option.trip_text, p.return_option.source_ids, p.return_option.to_be_set_by)
        for p in station.config.profiles
    }
    state = replace(station.state, return_options=options)
    assert state.return_option(None, "mars_surface") is None
    assert state.return_option(None, "station_leo") == options["station_leo"]


@pytest.mark.trace("T-E23-064", "CONTRACTS594", "C1")
def test_a_second_confirm_by_the_same_person_before_the_count_is_confirmation_required() -> None:
    two = build_station(_two_confirmations())
    start(two)
    proposal = propose(two)
    counted = confirm(two, crew("per_a"), proposal)
    assert counted.state == "proposed" and counted.confirmations_required == 2
    with pytest.raises(ConfirmationRequired) as caught:
        confirm(two, crew("per_a"), counted)
    assert caught.value.profile_id == proposal.profile_id and caught.value.code == "CONFIRMATION_REQUIRED"
    assert confirm(two, crew("per_b"), counted).state == "active"


def _two_confirmations() -> dict[ConfigName, bytes]:
    from e23m_helpers import documents, edited

    return edited(
        documents(),
        ConfigName.ENVIRONMENT_PROFILES,
        lambda d: d["change_confirmation"].__setitem__("confirmations_required", 2),
    )


@pytest.mark.trace("T-E23-068", "DE23-13", "V66", "D16")
def test_activation_stamps_the_cutoff_instant_and_calls_the_participant_once_per_activation(
    station: Station,
) -> None:
    start(station)
    proposal = propose(station)
    station.advance(minutes=7)
    activated = confirm(station, crew(), proposal)
    assert activated.activated_at == station.clock.at  # readings before this instant are never current
    assert [call[0] for call in station.spy.calls] == [
        "station_leo",
        "lunar_surface",
    ]  # init, then the switch
    assert len(station.spy.calls) == 2


@pytest.mark.trace("T-E23-069", "3.3", "I-M3", "D1")
def test_a_release_between_proposal_and_confirmation_keeps_the_policy_pin_and_uses_new_lists() -> None:
    from e23m_helpers import documents, edited, make_release

    station = build_station()
    start(station)
    proposal = propose(station)
    raw = edited(
        documents(),
        ConfigName.ENVIRONMENT_PROFILES,
        lambda d: d["change_confirmation"].__setitem__("confirmations_required", 3),
    )
    raw = edited(raw, ConfigName.SENSOR_LIMITS, lambda d: d["limits"].pop(0))
    newer = make_release("rel_test_0002", raw)
    with station.run(crew().actor, release=newer) as uow:
        activated = act_on_change(
            station.services,
            uow,
            crew(),
            profile_id=proposal.profile_id,
            action="confirm",
            expected_revision=proposal.revision,
        )
    assert (
        proposal.confirmations_required == 1 and activated.state == "active"
    )  # the pinned count still applied
    assert activated.proposal_release_id == "rel_test_0001" and activated.config_release_id == "rel_test_0002"
    assert "co2_1h_act" not in activated.lists.limit_ids  # lists come from the release active at activation
    audit_types = [a.event_type for a in station.factory.state.audit]
    assert (
        "mission.environment_activated" in audit_types
    )  # the own confirmation is audited with the activation


@pytest.mark.trace("T-E23-073", "3.3")
def test_before_init_ports_return_none_and_commands_fail_retryably(station: Station) -> None:
    assert station.state.current() is None and station.state.mission_mode() is None
    assert station.state.active_environment_id() is None
    for call in (lambda: propose(station), lambda: confirm(station, crew(), _placeholder(station))):
        with pytest.raises(NotInitialized) as caught:
            call()
        assert caught.value.code == "INTERNAL_ERROR"
    assert json.dumps(station.factory.state.events, default=str) == "[]"  # nothing was written


def _placeholder(station: Station) -> rules.ProfileRecord:
    template = rules.ProfileTemplate("station_leo", "L", "p", "w", "d", 1)
    return rules.start_profile(
        profile_id="env_x",
        template=template,
        actor=rules.ActorRef("operator", "o"),
        now=START,
        confirmations_required=1,
        release_id="r",
        lists=rules.ProfileLists((), (), (), (), (), ()),
    )

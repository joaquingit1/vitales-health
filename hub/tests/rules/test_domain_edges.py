"""Defensive and boundary behavior of the pure domain.

Every case here is a way an input, a view or a candidate definition can be unusual. Each one must fail
closed: no raise from a half-known definition, no clear from an incomparable value, a typed error for
an invalid value, never a bare exception (AGENTS.md section 6 failure cases).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
import rules_harness as h

from crew_health.kernel.units import Quantity, Unit
from crew_health.rules.domain import clear_evidence, dwell, hold, origin, time_policy
from crew_health.rules.domain.decisions import Clear, Refuse
from crew_health.rules.domain.definitions import (
    RulesDocument,
    parse_definition,
    parse_rules_document,
)
from crew_health.rules.domain.engine import evaluate, reconcile_clear_events
from crew_health.rules.domain.errors import (
    ActionNotAllowed,
    DefinitionInvalid,
    InvalidInput,
    InvariantViolation,
    LevelNotAllowed,
    OriginMismatch,
)
from crew_health.rules.domain.fingerprint import ReleaseDocuments, rule_fingerprint
from crew_health.rules.domain.inputs import (
    AnswerInput,
    ChannelRenderFacts,
    GroundLinkInput,
    LimitResult,
    StateFactInput,
    SupplyCoverInput,
    SupplyCoverRow,
)
from crew_health.rules.domain.vocabulary import (
    ACTIVE,
    ClearReason,
    EnvironmentId,
    Level,
    OriginMode,
    RaisedBy,
    Scope,
    WarningState,
)
from crew_health.rules.domain.warning import (
    AssistantActor,
    Dismissal,
    PersonActor,
    Resolution,
    SessionActor,
    acknowledge,
    attach_overrides,
    resolve,
)

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "INV-E24-14", "INV-E24-16", "INV-E24-07")]

CROSSED = {"crossed": True, "clear_holds": False}


def at(minutes: int) -> datetime:
    return cast(datetime, h.T0 + timedelta(minutes=minutes))


def scratch(
    make_world: Callable[..., h.World], mutate: Callable[[dict[str, object]], None], **kw: object
) -> h.World:
    raw = copy.deepcopy(h.deployment_rules())
    mutate(raw)
    return make_world(parse_rules_document(raw), **kw)


def edit_definition(definition_id: str, **changes: object) -> Callable[[dict[str, object]], None]:
    def mutate(raw: dict[str, object]) -> None:
        for d in raw["definitions"]:  # type: ignore[attr-defined]
            if d["id"] == definition_id:
                for key, value in changes.items():
                    d[key] = value

    return mutate


def condition(definition_id: str, **changes: object) -> Callable[[dict[str, object]], None]:
    def mutate(raw: dict[str, object]) -> None:
        for d in raw["definitions"]:  # type: ignore[attr-defined]
            if d["id"] == definition_id:
                d["condition"].update(changes)

    return mutate


# --- a half-known definition raises nothing ----------------------------------------------------------


def test_a_definition_without_its_fingerprints_is_never_raised(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument
) -> None:
    def threshold(raw: dict[str, object]) -> None:
        condition("ground_silence", after_next_contact_seconds=60)(raw)
        condition("supply_cover_low", below_uses=3)(raw)

    w = scratch(make_world, threshold, environment=EnvironmentId.MARS_SURFACE)
    w.answer_events = (w.event("evt_land", "landing", at(-60)),)
    row = SupplyCoverRow(
        "s1",
        "name",
        1,
        "p",
        1,
        "rel_a",
        (),
        Quantity(Decimal("2"), Unit("g")),
        Quantity(Decimal("1"), Unit("g")),
        None,
        None,
    )
    inputs = [
        w.sensor(results=[h.result("co2_1h_act", **CROSSED)]),
        w.freshness_input("stale", since=h.T0),
        w.event("evt_a", "space_weather_alert", h.T0),
        StateFactInput("local_water_untested", True, "mars_surface", h.T0, "evt_f", OriginMode.LIVE),
        GroundLinkInput("down", at(-30), "adapter", "mars_surface", False, h.T0),
        SupplyCoverInput("mars_surface", "use", "use_1", h.T0, OriginMode.LIVE, (row,)),
        AnswerInput("ans_1", "p1", "dizzy_standing", "present", h.T0, h.T0, OriginMode.LIVE, "d", "t"),
        h.insight_input(),
    ]
    w.now = at(30)
    for inp in inputs:
        view = replace(w.view(), fingerprints={})
        assert evaluate(inp, view) == (), type(inp).__name__
    assert w.feed(inputs[0]) != ()  # with the fingerprints present the same input raises
    assert deployment_doc.definitions


def test_missing_origin_words_and_missing_channel_facts_still_render_what_is_known(world: h.World) -> None:
    bare = ChannelRenderFacts("CO2", None, "Live sensor", None, None)
    inp = replace(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]), channel=bare)
    view = replace(world.view(), origin_words=None)
    (raise_step,) = evaluate(inp, view)
    placeholders = raise_step.render.placeholders  # type: ignore[union-attr]
    assert "location" not in placeholders and "last_received_time" not in placeholders
    event = replace(world.view(), origin_words=None)
    storm = [s for s in evaluate(world.event("evt_a", "space_weather_alert", h.T0), event)]
    assert storm and "source_label" not in storm[0].render.placeholders  # type: ignore[union-attr]
    unknown_origin = replace(world.view(), origin_words={"SIM": "Simulated"})
    storm = [s for s in evaluate(world.event("evt_b", "space_weather_alert", h.T0), unknown_origin)]
    assert "source_label" not in storm[0].render.placeholders  # type: ignore[union-attr]


def test_a_crossed_result_for_another_sensor_type_or_an_unknown_limit_raises_nothing(world: h.World) -> None:
    other_type = world.sensor(sensor_type="o2", channel="ch_o2", results=[h.result("co2_1h_act", **CROSSED)])
    assert world.feed(other_type) == ()
    nothing = world.sensor(results=[h.result("limit_that_does_not_exist", **CROSSED)])
    assert world.feed(nothing) == ()


def test_a_frozen_view_drops_the_dwell_marker_of_a_warning_it_cannot_clear(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    world.now = at(1)
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=False, clear_holds=True)]))
    assert world.dwell
    world.frozen = True
    world.now = at(2)
    decision = world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=False, clear_holds=True)]))
    assert [type(s).__name__ for s in decision] == ["DeleteDwell"] and not world.dwell


def test_reconcile_skips_warnings_that_clear_by_other_means(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    assert reconcile_clear_events(world.view()) == ()
    world.feed(world.event("evt_a", "space_weather_alert", at(0)))
    assert reconcile_clear_events(world.view()) == ()  # an outstanding occurrence: not reconciled


# --- quiet channels ----------------------------------------------------------------------------------


def test_a_quiet_warning_clears_only_on_fresh_evidence_after_the_raise_of_its_own_origin(
    world: h.World,
) -> None:
    world.feed(world.freshness_input("stale", since=h.T0))
    (quiet,) = world.by_definition("sensor_quiet")
    world.now = at(10)

    def attempt(**kw: object) -> tuple[object, ...]:
        return evaluate(world.freshness_input("current", **kw), world.view())

    assert attempt() == ()  # no observed time at all
    assert attempt(obs=h.observed(at(9), state="unknown")) == ()  # an unknown time never clears
    assert attempt(obs=h.observed(h.T0 - timedelta(minutes=1))) == ()  # observed before the raise
    assert attempt(obs=h.observed(at(11))) == ()  # a reading from the future
    assert (
        attempt(origin=OriginMode.SIM, obs=h.observed(at(9), origin=OriginMode.SIM)) == ()
    )  # another origin
    ok = attempt(obs=h.observed(at(9)))
    assert [type(s) for s in ok] == [Clear] and cast(Clear, ok[0]).warning_id == quiet.warning_id
    world.frozen = True
    assert attempt(obs=h.observed(at(9))) == ()
    world.frozen = False
    assert (
        evaluate(world.freshness_input("stale", since=at(10)), world.view()) == ()
    )  # already active for the channel


def test_a_stale_channel_without_a_last_reading_raises_with_no_evidence_and_a_spent_trigger_is_refused(
    world: h.World,
) -> None:
    inp = replace(world.freshness_input("stale", since=h.T0), last_reading_id=None)
    world.feed(inp)
    (quiet,) = world.by_definition("sensor_quiet")
    assert quiet.evidence == ()
    world.apply((Clear(quiet.warning_id, ClearReason.CONDITION_FALSE),))
    again = world.feed(inp)
    assert [type(s) for s in again] == [Refuse] and again[0].code == "trigger_spent"


def test_a_replay_quiet_warning_clears_only_on_a_receive_reference_after_the_raise(world: h.World) -> None:
    replay = OriginMode.REPLAY
    world.feed(world.freshness_input("stale", since=h.T0, origin=replay))
    (quiet,) = world.by_definition("sensor_quiet")
    world.now = at(5)
    none = world.freshness_input("current", origin=replay)
    assert evaluate(none, world.view()) == ()
    early = world.freshness_input("current", origin=replay, receive_reference=h.T0)
    assert evaluate(early, world.view()) == ()
    ok = world.freshness_input("current", origin=replay, receive_reference=at(4))
    assert [type(s) for s in evaluate(ok, world.view())] == [Clear] and quiet.active


# --- mission events keyed by person and by place -----------------------------------------------------


def test_a_person_scope_event_keys_by_person_and_refuses_a_foreign_origin(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, edit_definition("radiation_storm_shelter", scope="person"))
    w.feed(w.event("evt_a", "space_weather_alert", at(0), persons=("p2", "p1", "p1")))
    assert sorted(x.scope_key for x in w.warnings.values()) == ["p1", "p2"]
    assert all(x.person_id == x.scope_key for x in w.warnings.values())
    assert w.feed(w.event("evt_none", "space_weather_alert", at(1))) == ()  # names nobody: nothing to key
    sim = w.feed(w.event("evt_sim", "space_weather_alert", at(2), origin=OriginMode.SIM, persons=("p3",)))
    assert [type(s) for s in sim] == [Refuse] and sim[0].code == "origin_mismatch"
    w.now = at(30)
    w.feed(w.event("evt_c", "space_weather_all_clear", at(30), persons=("p1",)))
    assert [x.scope_key for x in w.warnings.values() if x.active] == [
        "p2"
    ]  # only the named person's warning clears


def test_a_habitat_scope_event_keys_by_location_or_environment_and_carries_the_location(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, edit_definition("radiation_storm_shelter", scope="habitat"))
    w.feed(w.event("evt_a", "space_weather_alert", at(0), location="airlock"))
    w.feed(w.event("evt_b", "space_weather_alert", at(1)))
    keys = {x.scope_key: x.location for x in w.warnings.values()}
    assert keys == {"airlock": "airlock", "environment:station_leo": None}
    w.now = at(30)
    w.feed(w.event("evt_c", "space_weather_all_clear", at(30), location="airlock"))
    assert [x.scope_key for x in w.warnings.values() if x.active] == ["environment:station_leo"]


def test_a_state_fact_on_a_crew_scope_and_an_unscoped_person_fact(make_world: Callable[..., h.World]) -> None:
    w = scratch(
        make_world,
        edit_definition("local_water_untested", scope="crew"),
        environment=EnvironmentId.MARS_SURFACE,
    )
    w.feed(StateFactInput("local_water_untested", True, "mars_surface", h.T0, "evt_1", OriginMode.LIVE))
    (found,) = w.by_definition("local_water_untested")
    assert found.scope_key == ""
    assert (
        w.feed(StateFactInput("local_water_untested", True, "mars_surface", h.T0, "evt_2", OriginMode.LIVE))
        == ()
    )
    w.apply((Clear(found.warning_id, ClearReason.CONDITION_FALSE),))
    spent = w.feed(
        StateFactInput("local_water_untested", True, "mars_surface", h.T0, "evt_1", OriginMode.LIVE)
    )
    assert [type(s) for s in spent] == [Refuse] and spent[0].code == "trigger_spent"
    person = scratch(
        make_world,
        edit_definition("local_water_untested", scope="person"),
        environment=EnvironmentId.MARS_SURFACE,
    )
    assert (
        person.feed(
            StateFactInput("local_water_untested", True, "mars_surface", h.T0, "evt_3", OriginMode.LIVE)
        )
        == ()
    )
    str_world = scratch(
        make_world, condition("local_water_untested", equals="wet"), environment=EnvironmentId.MARS_SURFACE
    )
    assert str_world.feed(
        StateFactInput("local_water_untested", "wet", "mars_surface", h.T0, "evt_4", OriginMode.LIVE)
    )
    assert (
        str_world.feed(
            StateFactInput("local_water_untested", "dry", "mars_surface", at(5), "evt_5", OriginMode.LIVE)
        )
        != ()
    )
    num = scratch(
        make_world, condition("local_water_untested", equals=2.5), environment=EnvironmentId.MARS_SURFACE
    )
    assert num.feed(
        StateFactInput("local_water_untested", 2.5, "mars_surface", h.T0, "evt_6", OriginMode.LIVE)
    )


def test_an_answer_for_another_question_or_presence_raises_nothing(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument
) -> None:
    w = make_world(deployment_doc, environment=EnvironmentId.MARS_SURFACE)
    w.answer_events = (w.event("evt_land", "landing", at(-60)),)
    other = AnswerInput("a1", "p1", "other_question", "present", h.T0, h.T0, OriginMode.LIVE, "d", "t")
    declined = AnswerInput("a2", "p1", "dizzy_standing", "declined", h.T0, h.T0, OriginMode.LIVE, "d", "t")
    assert w.feed(other) == () and w.feed(declined) == ()
    named = replace(w.answer_events[0], person_ids=("p9",))  # an event naming someone else does not count
    w.answer_events = (named,)
    assert (
        w.feed(AnswerInput("a3", "p1", "dizzy_standing", "present", h.T0, h.T0, OriginMode.LIVE, "d", "t"))
        == ()
    )


# --- ground silence and supply -----------------------------------------------------------------------


def test_ground_silence_spent_trigger_and_an_unreadable_pinned_contact_never_clear(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, condition("ground_silence", after_next_contact_seconds=60))
    w.now = at(10)
    link = GroundLinkInput("down", at(0), "adapter", "station_leo", False, at(10))
    w.feed(link)
    (g,) = w.by_definition("ground_silence")
    broken = replace(g, trigger=replace(g.trigger, ref="not a time"))
    w.warnings[g.warning_id] = broken
    w.now = at(20)
    assert (
        w.feed(GroundLinkInput("up", None, "adapter", "station_leo", False, at(20))) == ()
    )  # unreadable: stays open
    w.warnings[g.warning_id] = g
    w.apply((Clear(g.warning_id, ClearReason.CONDITION_FALSE),))
    again = w.feed(link)
    assert [type(s) for s in again] == [Refuse] and again[0].code == "trigger_spent"


def test_ground_silence_without_a_contact_time_schedules_nothing(make_world: Callable[..., h.World]) -> None:
    w = scratch(make_world, condition("ground_silence", after_next_contact_seconds=60))
    assert w.feed(GroundLinkInput("down", None, "adapter", "station_leo", False, h.T0)) == ()


def test_supply_quantities_and_the_release_trigger_when_no_use_is_named(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, condition("supply_cover_low", below_uses=3))
    row = SupplyCoverRow(
        "s1",
        "kit",
        1,
        "p",
        4,
        "rel_9",
        (),
        Quantity(Decimal("12.5"), Unit("g")),
        Quantity(Decimal("2"), Unit("g")),
        None,
        None,
    )
    w.now = at(1)
    w.feed(SupplyCoverInput("station_leo", "release", None, at(1), OriginMode.LIVE, (row,)))
    (s,) = w.by_definition("supply_cover_low")
    assert s.trigger.ref == "rel_9:station_leo" and s.evidence == ()
    assert "12.5 g" in s.what and "2 g" in s.what
    w.apply((Clear(s.warning_id, ClearReason.CONDITION_FALSE),))
    again = w.feed(SupplyCoverInput("station_leo", "release", None, at(2), OriginMode.LIVE, (row,)))
    assert [type(x) for x in again] == [Refuse] and again[0].code == "trigger_spent"
    unrelated = SupplyCoverRow("zz", "x", 9, "p", 1, "r", (), None, None, None, None)
    assert w.feed(SupplyCoverInput("station_leo", "use", "u", at(3), OriginMode.LIVE, (unrelated,))) == ()


# --- insights ----------------------------------------------------------------------------------------


def insight_world() -> h.World:
    return h.World(parse_rules_document(h.deployment_rules()), h.release_documents())


def test_insight_edges_unlisted_definition_policy_level_and_spent_trigger() -> None:
    w = insight_world()
    from crew_health.rules.domain.inputs import AssistantPolicy, InsightInput, InsightTopic

    policy = h.assistant_policy()
    missing = AssistantPolicy(
        policy.allowed_warning_levels, (InsightTopic("sleep_reaction", "no_such_definition", "eyes"),)
    )
    assert w.feed(InsightInput(h.insight_draft(), ((h.OBS_KEY, h.good_record()),), missing)) == ()
    narrow = AssistantPolicy(frozenset({Level.INFO}), policy.topics)
    rejected = w.feed(
        InsightInput(h.insight_draft(level=Level.INFO), ((h.OBS_KEY, h.good_record()),), narrow)
    )
    assert rejected[0].codes == ("level_not_allowed",)
    w.feed(h.insight_input())
    (first,) = w.by_definition("assistant_pattern")
    w.apply((Clear(first.warning_id, ClearReason.EXPIRED),))
    again = w.feed(h.insight_input())
    assert [type(s) for s in again] == [Refuse] and again[0].code == "trigger_spent"


# --- entity invariants and transitions ---------------------------------------------------------------


def co2(world: h.World):  # type: ignore[no-untyped-def]
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (found,) = world.by_definition("co2_above_limit")
    return found


def test_every_inconsistent_warning_value_is_refused(world: h.World) -> None:
    w = co2(world)
    ack = acknowledge(w, PersonActor("p1"), h.T0)
    bad: list[tuple[type[Exception], dict[str, object]]] = [
        (InvariantViolation, {"revision": 0}),
        (InvariantViolation, {"title": ""}),
        (InvariantViolation, {"cleared_at": h.T0}),
        (InvariantViolation, {"state": WarningState.RESOLVED}),
        (InvariantViolation, {"state": WarningState.DISMISSED}),
        (InvariantViolation, {"insight_id": "ins_1"}),
        (OriginMismatch, {"scope": Scope.PERSON, "person_id": "p1", "origin_mode": OriginMode.REPLAY}),
        (LevelNotAllowed, {"raised_by": RaisedBy.ASSISTANT, "level": Level.EMERGENCY, "insight_id": "i"}),
    ]
    for error, change in bad:
        with pytest.raises(error):
            replace(w, **change)
    with pytest.raises(InvariantViolation):
        replace(ack, acknowledgements=())  # acknowledged needs an acknowledgement
    with pytest.raises(ActionNotAllowed):  # an assistant warning is never resolved
        replace(
            w,
            raised_by=RaisedBy.ASSISTANT,
            insight_id="i",
            level=Level.WATCH,
            state=WarningState.RESOLVED,
            resolved=Resolution("p", "person", h.T0, "r", None),
        )
    with pytest.raises(ActionNotAllowed):  # a rule warning is never dismissed
        replace(w, state=WarningState.DISMISSED, dismissed=Dismissal("p", h.T0, None))


def test_resolution_and_acknowledgement_reject_non_person_and_assistant_actors(world: h.World) -> None:
    w = co2(world)
    with pytest.raises(ActionNotAllowed):
        acknowledge(w, SessionActor("operator", "op"), h.T0)
    with pytest.raises(ActionNotAllowed):
        resolve(w, AssistantActor("a"), h.T0, "r", role=None)
    assert attach_overrides(attach_overrides(w, ("ovr_b", "ovr_a")), ("ovr_a",)).routine_override_ids == (
        "ovr_a",
        "ovr_b",
    )
    same = attach_overrides(w, ())
    assert same is w  # nothing to attach: no new revision


def test_a_person_warning_resolution_of_an_assistant_warning_is_refused_by_the_transition() -> None:
    w = insight_world()
    w.feed(h.insight_input())
    (assistant,) = w.by_definition("assistant_pattern")
    assert assistant.raised_by is RaisedBy.ASSISTANT and assistant.state in ACTIVE
    with pytest.raises(ActionNotAllowed):
        resolve(assistant, PersonActor("p1"), h.T0, "reason", role=None)


# --- clear evidence, dwell, origin, time policy, hold, inputs ----------------------------------------


def test_eligibility_needs_a_pinned_limit_and_a_present_result(world: h.World) -> None:
    world.feed(world.freshness_input("stale", since=h.T0))
    (quiet,) = world.by_definition("sensor_quiet")
    inp = world.sensor(results=[h.result("co2_1h_act", crossed=False, clear_holds=True)])
    assert (
        clear_evidence.eligible(inp, quiet, world.now).reason == "pin_unavailable"
    )  # a quiet warning has no limit pin
    assert clear_evidence.pinned_result(inp, quiet) is None
    w = co2(world)
    absent = world.sensor(at=at(1), results=[])
    assert clear_evidence.eligible(absent, w, at(1)).reason == "limit_missing"
    crossed_and_holding = world.sensor(
        at=at(1), results=[h.result("co2_1h_act", crossed=True, clear_holds=True)]
    )
    assert (
        clear_evidence.eligible(crossed_and_holding, w, at(1)).reason == "clear_not_holding"
    )  # contradictory: fail closed


def test_a_replay_input_without_a_data_time_is_not_evidence(world: h.World) -> None:
    from crew_health.rules.domain.vocabulary import OriginMode as Mode

    world.feed(world.sensor(origin=Mode.REPLAY, results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    inp = world.sensor(
        origin=Mode.REPLAY,
        results=[h.result("co2_1h_act", crossed=False, clear_holds=True)],
        obs=h.observed(h.T0, origin=Mode.REPLAY, state="unknown"),
    )
    assert clear_evidence.eligible(inp, w, world.now).reason == "time_unknown"


def test_a_marker_of_another_rule_is_void_and_replaced_or_deleted() -> None:
    marker = dwell.DwellMarker("w", "c", "old", h.T0, h.T0, "seg")
    kw = {
        "warning_id": "w",
        "channel_id": "c",
        "segment_key": "seg",
        "dwell_seconds": 600,
        "gap_bound_seconds": 300,
        "fingerprint": "new",
    }
    replaced = dwell.step(marker, e=at(1), eligible=True, **kw)  # type: ignore[arg-type]
    assert (
        replaced.action == "put"
        and replaced.marker is not None
        and replaced.marker.holding_since == at(1)
        and not replaced.clear
    )
    deleted = dwell.step(marker, e=None, eligible=False, **kw)  # type: ignore[arg-type]
    assert deleted.action == "delete"
    assert dwell.step(None, e=None, eligible=False, **kw).action == "none"  # type: ignore[arg-type]
    zero = dwell.step(None, e=at(1), eligible=True, **{**kw, "dwell_seconds": 0})  # type: ignore[arg-type]
    assert zero.clear  # a dwell of zero clears on the first eligible evaluation


def test_origin_rules() -> None:
    assert not origin.person_origin_allowed(evidence_origins=[], mission_mode=OriginMode.LIVE)
    assert origin.person_origin_allowed(
        evidence_origins=[OriginMode.LIVE, OriginMode.LIVE], mission_mode=OriginMode.LIVE
    )
    assert not origin.person_origin_allowed(
        evidence_origins=[OriginMode.REPLAY], mission_mode=OriginMode.REPLAY
    )
    assert origin.ground_origin("demo_switch", OriginMode.LIVE) is OriginMode.SIM
    assert origin.ground_origin("adapter", OriginMode.REPLAY) is OriginMode.REPLAY


def test_time_policy_edges() -> None:
    now = at(0)
    assert not time_policy.ground_silence_holds(
        now=now, status="down", next_contact_at=None, threshold_seconds=1, gap_planned=False
    )
    assert time_policy.ground_silence_ended(status="down", next_contact_at=at(5), raised_for=at(1))
    assert not time_policy.ground_silence_ended(status="down", next_contact_at=None, raised_for=at(1))
    assert not time_policy.ground_silence_ended(status="down", next_contact_at=at(1), raised_for=at(1))
    assert not time_policy.expiry_due(
        elapsed_seconds=10**9, expires_after_seconds=None, clock_regressed=False
    )
    assert not time_policy.answer_window_satisfied(
        answered_at=at(0), answered_corrected_at=None, occurred_at=at(5), occurred_corrected_at=None, days=1
    )
    assert not time_policy.answer_window_satisfied(
        answered_at=at(0), answered_corrected_at=at(0), occurred_at=at(5), occurred_corrected_at=at(6), days=1
    )


def test_act_routing_with_nobody_asleep_and_no_disclosure_notifies_no_one() -> None:
    routing = hold.route_act(
        sleepers=[],
        awake_responders=["p1"],
        acknowledged_by=[],
        elapsed_seconds=0,
        wake_after_seconds=300,
        responders_may_receive=False,
    )
    assert routing.notify_now == () and routing.wake_now == ()
    open_to_all = hold.route_act(
        sleepers=[],
        awake_responders=["p1"],
        acknowledged_by=[],
        elapsed_seconds=0,
        wake_after_seconds=300,
        responders_may_receive=True,
    )
    assert open_to_all.notify_now == ("p1",)


def test_inputs_reject_invalid_values_with_a_typed_error(world: h.World) -> None:
    facts = h.render_facts()
    with pytest.raises(InvalidInput):
        LimitResult("l", True, False, None, Decimal("1.5"), None, facts)
    with pytest.raises(InvalidInput):
        replace(world.sensor(results=[]), stale_after_seconds=0)
    unknown = h.observed(h.T0, state="unknown")
    assert unknown.axis() is None and unknown.interval() is None
    assert unknown.display_at() == h.T0
    assert h.observed(h.T0, uncertainty_ms=250).interval() == (
        h.T0 - timedelta(milliseconds=250),
        h.T0 + timedelta(milliseconds=250),
    )


def test_level_comparison_with_a_foreign_value_is_not_implemented() -> None:
    assert Level.ACT.__lt__("x") is NotImplemented and Level.ACT.__le__("x") is NotImplemented
    assert Level.ACT.__gt__("x") is NotImplemented and Level.ACT.__ge__("x") is NotImplemented
    assert Level.ACT >= Level.ACT and Level.ACT <= Level.EMERGENCY


# --- definition parser failure paths -----------------------------------------------------------------


def base_raw() -> dict[str, object]:
    return copy.deepcopy(next(d for d in h.deployment_rules()["definitions"] if d["id"] == "co2_above_limit"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.__setitem__("raised_by", "rule"),  # a sensor condition raised by rule
        lambda d: d.__setitem__("raised_by", "assistant"),
        lambda d: d["condition"].update(
            {"kind": "mission_event", "event_types": ["x"], "clear_event_types": []}
        ),
        lambda d: d["content"]["steps"].update({"from": "assistant"}),
        lambda d: d.__setitem__("hazard_id", None),
        lambda d: d["content"].__setitem__("source_ids", ["EV01", "EV01"]),
        lambda d: d.__setitem__("environments", ["station_leo", "station_leo"]),
        lambda d: d.__setitem__("environments", ["not_an_environment"]),
        lambda d: d.__setitem__("environments", [1]),
        lambda d: d["content"].__setitem__("body_regions", ["head", "head"]),
        lambda d: d["content"]["headline"].__setitem__("rank", -1),
        lambda d: d["content"].__setitem__("title", 5),
        lambda d: d.__setitem__("level", 3),
        lambda d: d.__setitem__("hazard_level_index", True),
        lambda d: d.__setitem__("scope", "elsewhere"),
    ],
)
def test_parse_definition_rejects_each_malformed_definition(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    raw = base_raw()
    mutate(raw)
    with pytest.raises(DefinitionInvalid):
        parse_definition(raw)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: (
            d["condition"].update({"kind": "answer", "question_id": "q", "presence": ["maybe"]})
            or d.update(scope="person")
        ),
        lambda d: d["condition"].update({"kind": "state_fact", "fact": "f", "equals": {"a": 1}}),
        lambda d: d["condition"].update(
            {
                "kind": "ground_silence",
                "after_next_contact_seconds": 1,
                "basis": "sourced",
                "to_be_set_by": "nobody",
            }
        ),
        lambda d: (
            d["condition"].update({"kind": "answer", "question_id": "q", "presence": ["present"]})
            or d.update(scope="habitat")
        ),
        lambda d: d["content"]["steps"].update({"from": "listed", "items": [], "basis": "sourced"}),
        lambda d: d["content"]["steps"].update({"unexpected": 1}),
    ],
)
def test_parse_definition_rejects_malformed_conditions_and_scopes(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    raw = base_raw()
    mutate(raw)
    if raw["condition"]["kind"] in {"answer", "state_fact", "ground_silence"}:  # type: ignore[index]
        raw["raised_by"] = "rule"
    with pytest.raises(DefinitionInvalid):
        parse_definition(raw)


def test_parse_rules_document_rejects_empty_duplicate_and_non_object_documents() -> None:
    good = copy.deepcopy(h.deployment_rules())
    empty = {**good, "definitions": []}
    duplicate = {**good, "definitions": [good["definitions"][0], good["definitions"][0]]}
    bad_documents: tuple[object, ...] = (
        empty,
        duplicate,
        [],
        {**good, "headline": {"max_fragments": 0, "separator": ",", "none_text": "x"}},
        {**good, "headline": {"max_fragments": 1, "separator": 3, "none_text": "x"}},
    )
    for bad in bad_documents:
        with pytest.raises(DefinitionInvalid):
            parse_rules_document(bad)


def test_fingerprints_of_definitions_whose_dependencies_are_missing_are_still_deterministic(
    deployment_doc: RulesDocument,
) -> None:
    empty = ReleaseDocuments()
    first = {d.id: rule_fingerprint(d, empty) for d in deployment_doc.definitions}
    assert first == {d.id: rule_fingerprint(d, empty) for d in deployment_doc.definitions}
    assert len(set(first.values())) == len(first)
    odd = ReleaseDocuments(
        sensor_limits={
            "limits": ["not a mapping", {"limit_id": "co2_1h_act", "sensor_type_id": "co2", "clear": "bad"}]
        },
        sensor_catalog={"sensor_types": [{"id": "co2", "aggregations": "bad"}]},
        questionnaires={"questions": [{"id": "dizzy_standing", "choices": "bad"}]},
        assistant_policy={"insight_topics": "bad"},
    )
    assert all(rule_fingerprint(d, odd) for d in deployment_doc.definitions)

"""Mission events, answers, state facts, ground silence and supply cover (E24 3.5, 3.9, 3.11).

T-E24-035 to 038, 065, 066, 068, 075, 086, 089, 118, 119 and the D3 evidence-after-raise rule.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import timedelta
from typing import Literal, cast

import pytest
import rules_harness as h

from crew_health.rules.domain.decisions import (
    Attach,
    CancelDeadline,
    Clear,
    Refuse,
    ResolveOccurrences,
    ScheduleDeadline,
)
from crew_health.rules.domain.definitions import RulesDocument, parse_rules_document
from crew_health.rules.domain.engine import reconcile_clear_events
from crew_health.rules.domain.inputs import (
    AnswerInput,
    GroundLinkInput,
    StateFactInput,
    SupplyCoverInput,
    SupplyCoverRow,
)
from crew_health.rules.domain.vocabulary import (
    ClearReason,
    EnvironmentId,
    Level,
    OriginMode,
    WarningState,
)

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "INV-E24-08", "INV-E24-17")]

ALERT, ALL_CLEAR = "space_weather_alert", "space_weather_all_clear"


def at(minutes: int):  # type: ignore[no-untyped-def]
    return h.T0 + timedelta(minutes=minutes)


def feed_at(world: h.World, minutes: int, event_id: str, event_type: str, **kw: object) -> tuple[object, ...]:
    world.now = at(minutes)
    return cast(tuple[object, ...], world.feed(world.event(event_id, event_type, at(minutes), **kw)))


def storm(world: h.World):  # type: ignore[no-untyped-def]
    found = world.by_definition("radiation_storm_shelter")
    return found[0] if found else None


@pytest.mark.trace("T-E24-075", "D2", "V58")
def test_t075_the_storm_is_raised_at_emergency_by_an_alert_and_cleared_by_a_later_all_clear(
    world: h.World,
) -> None:
    feed_at(world, 0, "evt_a", ALERT)
    w = storm(world)
    assert w is not None and w.level is Level.EMERGENCY and w.scope.value == "crew"
    assert w.steps == (
        "Go to the storm shelter now.",
        "If you are outside, end the walk and come back in.",
        "Stay in the shelter until the all clear.",
    )
    assert w.evidence[0].summary == f"label {ALERT}"  # the event type's configured label, no absent source
    feed_at(world, 30, "evt_c", ALL_CLEAR)
    cleared = world.by_definition("radiation_storm_shelter", active_only=False)[0]
    assert cleared.state is WarningState.CLEARED and cleared.clear_reason is ClearReason.CLEAR_EVENT


@pytest.mark.trace("T-E24-118", "INV-E24-17", "H13")
def test_t118_worked_sequence_alerts_1000_and_1020_then_a_1010_all_clear_delivered_last(
    world: h.World,
) -> None:
    feed_at(world, 0, "evt_a", ALERT)
    feed_at(world, 20, "evt_b", ALERT)
    world.now = at(31)
    decision = world.feed(world.event("evt_c_early", ALL_CLEAR, at(10)))  # occurred 10:10, delivered last
    (w,) = world.by_definition("radiation_storm_shelter")
    assert any(isinstance(s, ResolveOccurrences) for s in decision)
    assert not any(isinstance(s, Clear) for s in decision)
    open_refs = [o.trigger_ref for o in world.occurrences[w.warning_id] if o.resolved_by_event_id is None]
    assert open_refs == ["evt_b"]  # the 10:00 alert is resolved, the 10:20 alert stays
    world.now = at(40)
    decision = world.feed(world.event("evt_c_late", ALL_CLEAR, at(30)))
    assert any(isinstance(s, Clear) and s.reason is ClearReason.CLEAR_EVENT for s in decision)
    assert not world.by_definition("radiation_storm_shelter")


@pytest.mark.trace("T-E24-035", "INV-E24-17")
def test_t035_an_all_clear_before_the_alert_or_at_the_same_time_never_clears(world: h.World) -> None:
    feed_at(world, 10, "evt_a", ALERT)
    world.now = at(20)
    world.feed(world.event("evt_early", ALL_CLEAR, at(0)))  # occurred before the alert, delivered late
    assert storm(world) is not None
    world.feed(world.event("evt_equal", ALL_CLEAR, at(10)))  # equal times stay outstanding (fail safe)
    assert storm(world) is not None


@pytest.mark.trace("T-E24-036", "INV-E24-08")
def test_t036_an_alert_whose_later_all_clear_is_already_recorded_raises_nothing(world: h.World) -> None:
    feed_at(world, 30, "evt_c", ALL_CLEAR)  # recorded first
    world.now = at(32)
    late = world.feed(world.event("evt_a_late", ALERT, at(0)))
    assert [type(s) for s in late] == [Refuse] and late[0].code == "clear_later"
    assert storm(world) is None


@pytest.mark.trace("T-E24-037", "T-E24-119", "INV-E24-08")
def test_t037_redelivery_changes_nothing_while_active_and_never_resurrects_after_final(
    world: h.World,
) -> None:
    feed_at(world, 0, "evt_a", ALERT)
    assert feed_at(world, 1, "evt_a", ALERT) == ()  # duplicate while active
    feed_at(world, 30, "evt_c", ALL_CLEAR)
    assert feed_at(world, 31, "evt_c", ALL_CLEAR) == ()  # duplicate clear: nothing to resolve
    again = feed_at(world, 32, "evt_a", ALERT)  # the spent alert delivered again after the clear
    assert [type(s) for s in again] == [Refuse] and cast(Refuse, again[0]).code == "trigger_spent"
    assert storm(world) is None
    feed_at(world, 40, "evt_new", ALERT)  # a new occurrence gets a new warning
    assert storm(world) is not None


def test_an_alert_attaches_as_outstanding_and_the_warning_record_is_unchanged(world: h.World) -> None:
    feed_at(world, 0, "evt_a", ALERT)
    (before,) = world.by_definition("radiation_storm_shelter")
    decision = feed_at(world, 20, "evt_b", ALERT)
    assert [type(s) for s in decision] == [Attach]
    assert world.warnings[before.warning_id] == before


def test_a_clear_resolved_while_frozen_is_reconciled_when_clears_unfreeze(world: h.World) -> None:
    feed_at(world, 0, "evt_a", ALERT)
    world.frozen = True
    decision = feed_at(world, 30, "evt_c", ALL_CLEAR)
    assert any(isinstance(s, ResolveOccurrences) for s in decision) and not any(
        isinstance(s, Clear) for s in decision
    )
    assert storm(world) is not None
    world.frozen = False
    reconcile = reconcile_clear_events(world.view())
    assert [type(s) for s in reconcile] == [Clear]
    assert reconcile_clear_events(h.World(world.rules, world.docs, frozen=True).view()) == ()


def test_an_all_clear_of_another_origin_never_clears_a_warning(world: h.World) -> None:
    feed_at(world, 0, "evt_a", ALERT)
    world.now = at(30)
    decision = world.feed(world.event("evt_c", ALL_CLEAR, at(30), origin=OriginMode.SIM))
    assert not any(isinstance(s, (Clear, ResolveOccurrences)) for s in decision)
    assert storm(world) is not None


# --- answers (dizziness after landing) ---------------------------------------------------------------


def answer(
    world: h.World,
    answer_id: str,
    minutes: int,
    *,
    presence: str = "present",
    origin: OriginMode = OriginMode.LIVE,
) -> AnswerInput:
    world.now = at(minutes)
    return AnswerInput(
        answer_id, "p1", "dizzy_standing", presence, at(minutes), at(minutes), origin, "day 3", "09:10"
    )


def mars(make_world: Callable[..., h.World], doc: RulesDocument) -> h.World:
    return make_world(doc, environment=EnvironmentId.MARS_SURFACE)


def test_an_answer_within_the_window_raises_a_person_warning_resolvable_never_cleared(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument
) -> None:
    w = mars(make_world, deployment_doc)
    w.answer_events = (w.event("evt_land", "landing", at(-2 * 24 * 60)),)
    w.feed(answer(w, "ans_1", 0))
    (warning,) = w.by_definition("dizzy_after_landing")
    assert warning.person_id == "p1" and warning.scope_key == "p1" and warning.level is Level.WATCH
    assert warning.evidence[0].kind.value == "answer"
    assert w.feed(answer(w, "ans_2", 10, presence="absent")) == ()  # a later answer never clears it
    assert w.by_definition("dizzy_after_landing")


@pytest.mark.parametrize("days_after", [6])
def test_an_answer_outside_the_window_or_without_the_event_raises_nothing(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument, days_after: int
) -> None:
    w = mars(make_world, deployment_doc)
    w.answer_events = (w.event("evt_land", "landing", at(-days_after * 24 * 60)),)
    assert w.feed(answer(w, "ans_1", 0)) == ()
    w.answer_events = ()
    assert w.feed(answer(w, "ans_2", 1)) == ()


@pytest.mark.trace("T-E24-089", "3.11")
def test_t089_the_answer_window_across_a_recorded_correction_raises_if_either_difference_satisfies_it(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument
) -> None:
    w = mars(make_world, deployment_doc)
    landing = at(-6 * 24 * 60)  # six days before: outside the five day window as recorded
    w.answer_events = (
        w.event("evt_land", "landing", landing, corrected=at(-4 * 24 * 60)),
    )  # corrected: four days
    inp = AnswerInput(
        "ans_1", "p1", "dizzy_standing", "present", at(0), at(0), OriginMode.LIVE, "day 3", "09:10"
    )
    w.now = at(0)
    w.feed(inp)
    assert w.by_definition("dizzy_after_landing")


@pytest.mark.trace("V72", "INV-E24-09")
@pytest.mark.parametrize("origin", [OriginMode.REPLAY, OriginMode.SIM])
def test_a_person_warning_is_refused_for_evidence_outside_the_persons_namespace(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument, origin: OriginMode
) -> None:
    w = mars(make_world, deployment_doc)  # mission mode LIVE
    w.answer_events = (w.event("evt_land", "landing", at(-60)),)
    decision = w.feed(answer(w, "ans_1", 0, origin=origin))
    assert [type(s) for s in decision] == [Refuse] and decision[0].code == "origin_mismatch"
    assert not w.warnings


def test_a_resolved_answer_warning_is_spent_and_never_reraised_by_redelivery(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument
) -> None:
    w = mars(make_world, deployment_doc)
    w.answer_events = (w.event("evt_land", "landing", at(-60)),)
    w.feed(answer(w, "ans_1", 0))
    (warning,) = w.by_definition("dizzy_after_landing")
    w.finish(warning.warning_id, WarningState.RESOLVED)
    redelivered = w.feed(answer(w, "ans_1", 1))
    assert [type(s) for s in redelivered] == [Refuse] and redelivered[0].code == "trigger_spent"
    w.feed(answer(w, "ans_3", 2))  # a new answer raises a new warning
    assert w.by_definition("dizzy_after_landing")


# --- state facts -------------------------------------------------------------------------------------


def fact(
    w: h.World, value: object, minutes: int, cause: str, origin: OriginMode = OriginMode.LIVE
) -> StateFactInput:
    w.now = at(minutes)
    return StateFactInput("local_water_untested", value, "mars_surface", at(minutes), cause, origin)  # type: ignore[arg-type]


def test_a_state_fact_raises_and_clears_only_on_a_later_different_value_of_its_own_origin(
    make_world: Callable[..., h.World], deployment_doc: RulesDocument
) -> None:
    w = mars(make_world, deployment_doc)
    w.feed(fact(w, True, 0, "evt_added"))
    (warning,) = w.by_definition("local_water_untested")
    assert warning.scope_key == "environment:mars_surface"
    assert (
        w.feed(fact(w, 1, 1, "evt_int")) == ()
    )  # an integer 1 never equals boolean true, and does not clear it
    w.now = at(-5)
    assert (
        w.feed(
            StateFactInput(
                "local_water_untested", False, "mars_surface", at(-5), "evt_early", OriginMode.LIVE
            )
        )
        == ()
    )
    assert (
        w.feed(
            StateFactInput("local_water_untested", False, "mars_surface", at(5), "evt_sim", OriginMode.SIM)
        )
        == ()
    )
    w.now = at(10)
    w.feed(
        StateFactInput("local_water_untested", False, "mars_surface", at(10), "evt_tested", OriginMode.LIVE)
    )
    assert w.warnings[warning.warning_id].clear_reason is ClearReason.CONDITION_FALSE


# --- ground silence ----------------------------------------------------------------------------------


def scratch(
    make_world: Callable[..., h.World], mutate: Callable[[dict[str, object]], None], **kw: object
) -> h.World:
    raw = copy.deepcopy(h.deployment_rules())
    mutate(raw)
    return make_world(parse_rules_document(raw), **kw)


def with_threshold(seconds: int | None) -> Callable[[dict[str, object]], None]:
    def mutate(raw: dict[str, object]) -> None:
        for d in raw["definitions"]:  # type: ignore[attr-defined]
            if d["id"] == "ground_silence":
                d["condition"]["after_next_contact_seconds"] = seconds

    return mutate


def link(
    status: Literal["up", "delayed", "down"],
    contact_minutes: int | None,
    observed: int,
    *,
    planned: bool = False,
    source: str = "adapter",
) -> GroundLinkInput:
    return GroundLinkInput(
        status,
        None if contact_minutes is None else at(contact_minutes),
        source,
        "station_leo",
        planned,
        at(observed),
    )


@pytest.mark.trace("T-E24-086", "3.11")
def test_t086_ground_silence_forward_correction_may_raise_and_backward_never_clears(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, with_threshold(600))  # ten minutes: a scratch value; the shipped one stays null
    w.now = at(5)
    decision = w.feed(link("down", 0, 5))
    assert any(
        isinstance(s, ScheduleDeadline) and s.kind == "absolute" and s.due_at == at(10) for s in decision
    )
    assert not w.warnings
    w.now = at(10)  # the deadline, or a forward correction of the clock, reaches the due instant
    w.feed(link("down", 0, 10))
    (g,) = w.by_definition("ground_silence")
    assert g.trigger.ref == at(0).isoformat() and g.evidence == ()
    w.now = at(-60)  # a backward correction of the clock
    decision = w.feed(link("down", 0, -60))
    assert not any(isinstance(s, Clear) for s in decision) and w.by_definition("ground_silence")
    w.now = at(20)
    w.feed(link("up", None, 20))
    assert w.warnings[g.warning_id].clear_reason is ClearReason.CONDITION_FALSE


def test_ground_silence_raises_nothing_without_a_threshold_or_for_a_planned_gap(
    make_world: Callable[..., h.World], world: h.World
) -> None:
    world.now = at(60)
    assert world.feed(link("down", 0, 60)) == ()  # the shipped threshold is null
    w = scratch(make_world, with_threshold(60))
    w.now = at(60)
    decision = w.feed(link("down", 0, 60, planned=True))
    assert not w.warnings and not any(isinstance(s, ScheduleDeadline) for s in decision)
    assert any(isinstance(s, CancelDeadline) for s in decision)


def test_ground_silence_ends_when_the_next_contact_moves_later_and_demo_link_is_sim(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, with_threshold(60))
    w.now = at(5)
    w.feed(link("down", 0, 5, source="demo_switch"))
    (g,) = w.by_definition("ground_silence")
    assert g.origin_mode is OriginMode.SIM  # a demonstration switch is SIM in a LIVE mission
    w.now = at(6)
    w.feed(link("down", 30, 6, source="demo_switch"))
    assert w.warnings[g.warning_id].clear_reason is ClearReason.CONDITION_FALSE


# --- supply cover ------------------------------------------------------------------------------------


def row(supply: str, cover: int | None, revision: int, partition: str = "part_a") -> SupplyCoverRow:
    return SupplyCoverRow(
        supply, f"name {supply}", cover, partition, revision, "rel_a", ("src_1",), None, None, None, None
    )


def cover(
    w: h.World, minutes: int, *rows: SupplyCoverRow, cause: str = "use", use_id: str | None = "use_1"
) -> SupplyCoverInput:
    w.now = at(minutes)
    return SupplyCoverInput("station_leo", cause, use_id, at(minutes), OriginMode.LIVE, tuple(rows))  # type: ignore[arg-type]


def threshold(n: int | None) -> Callable[[dict[str, object]], None]:
    def mutate(raw: dict[str, object]) -> None:
        for d in raw["definitions"]:  # type: ignore[attr-defined]
            if d["id"] == "supply_cover_low":
                d["condition"]["below_uses"] = n

    return mutate


@pytest.mark.trace("T-E24-065", "T-E24-066", "T-E24-068", "INV-E24-14")
def test_supply_cover_null_changes_nothing_and_two_low_supplies_raise_two_warnings(
    make_world: Callable[..., h.World], world: h.World
) -> None:
    assert world.feed(cover(world, 0, row("s1", 0, 1))) == ()  # the shipped threshold is null (Q19)
    w = scratch(make_world, threshold(3))
    w.feed(cover(w, 0, row("s1", None, 1)))
    assert not w.warnings  # null cover raises nothing
    w.feed(cover(w, 1, row("s1", 2, 2), row("s2", 1, 2), row("s3", 5, 2)))
    assert sorted(x.scope_key for x in w.warnings.values()) == ["s1", "s2"]
    w.feed(cover(w, 2, row("s1", None, 3)))
    assert (
        len([x for x in w.warnings.values() if x.active]) == 2
    )  # a null cover after a low one changes nothing


@pytest.mark.trace("INV-E24-14", "D3")
def test_supply_clear_needs_newer_stock_of_the_same_partition_after_the_raise(
    make_world: Callable[..., h.World],
) -> None:
    w = scratch(make_world, threshold(3))
    w.feed(cover(w, 5, row("s1", 1, 7)))
    (s,) = w.by_definition("supply_cover_low")
    assert s.source_revision == 7 and s.source_partition == "part_a"
    for stale in (row("s1", 9, 7), row("s1", 9, 6), row("s1", 9, 9, partition="part_b")):
        w.feed(cover(w, 6, stale))
        assert w.warnings[s.warning_id].active  # an old batch, or another partition, never clears
    w.feed(cover(w, 7, row("s1", 9, 8)))
    assert w.warnings[s.warning_id].clear_reason is ClearReason.CONDITION_FALSE

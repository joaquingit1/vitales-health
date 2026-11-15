"""Engine totality and safety invariants over random input sequences (E24 12.2 T-E24-012).

Every sequence runs through the pure engine and the harness application of its decisions; after each
step the invariants of E24 3.17 that the domain can decide must hold.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain.decisions import Clear, Raise
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.engine import evaluate
from crew_health.rules.domain.inputs import (
    ActivationInput,
    AnswerInput,
    DeadlineInput,
    GroundLinkInput,
    InsightExpiredInput,
    RuleInput,
    SensorEvaluatedInput,
    StateFactInput,
    SupplyCoverInput,
    SupplyCoverRow,
)
from crew_health.rules.domain.vocabulary import (
    ASSISTANT_LEVELS,
    ConditionKind,
    EnvironmentId,
    OriginMode,
    RaisedBy,
    Scope,
    WarningState,
)

pytestmark = [pytest.mark.E24, pytest.mark.trace("T-E24-012", "INV-E24-05", "INV-E24-16", "INV-E24-04", "H9")]

ORIGINS = st.sampled_from(list(OriginMode))
FLAGS = st.sampled_from([(True, False), (False, True), (False, False), (None, None), (True, True)])
LIMITS = st.sampled_from(["co2_1h_act", "co2_7d_watch"])
O2 = st.sampled_from(["o2_low_watch", "o2_low_act", "o2_low_emergency", "o2_high_act"])

OPS = st.one_of(
    st.tuples(
        st.just("co2"),
        st.integers(1, 700),
        st.lists(st.tuples(LIMITS, FLAGS), min_size=1, max_size=2),
        ORIGINS,
        st.sampled_from(["trusted", "uncertain", "unknown"]),
        st.sampled_from(["current", "stale"]),
    ),
    st.tuples(
        st.just("o2"),
        st.integers(1, 700),
        st.lists(st.tuples(O2, FLAGS), min_size=1, max_size=4),
        ORIGINS,
        st.just("trusted"),
        st.just("current"),
    ),
    st.tuples(
        st.just("fresh"),
        st.integers(1, 700),
        st.sampled_from(["current", "stale", "never_reported"]),
        ORIGINS,
    ),
    st.tuples(
        st.just("event"),
        st.integers(1, 700),
        st.sampled_from(["space_weather_alert", "space_weather_all_clear", "landing"]),
        ORIGINS,
    ),
    st.tuples(st.just("answer"), st.integers(1, 700), st.sampled_from(["present", "absent"]), ORIGINS),
    st.tuples(st.just("fact"), st.integers(1, 700), st.sampled_from([True, False, 1, "x"]), ORIGINS),
    st.tuples(
        st.just("link"), st.integers(1, 700), st.sampled_from(["up", "down", "delayed"]), st.booleans()
    ),
    st.tuples(st.just("supply"), st.integers(1, 700), st.sampled_from([None, 0, 2, 9]), st.integers(0, 6)),
    st.tuples(
        st.just("misc"),
        st.integers(1, 700),
        st.sampled_from(["expire", "activate", "deadline"]),
        st.booleans(),
    ),
    st.tuples(
        st.just("toggle"),
        st.integers(1, 700),
        st.sampled_from(["freeze", "thaw", "regress", "correct"]),
        st.booleans(),
    ),
)


def build(world: h.World, op: tuple[object, ...], n: int) -> RuleInput | None:
    kind, delta = op[0], int(op[1])  # type: ignore[call-overload]
    world.advance(delta)
    now = world.now
    if kind in {"co2", "o2"}:
        results = [h.result(limit, crossed=c, clear_holds=k) for limit, (c, k) in op[2]]  # type: ignore[misc,union-attr]
        sensor, channel = ("co2", "ch_co2") if kind == "co2" else ("o2", "ch_o2")
        return world.sensor(
            sensor_type=sensor,
            channel=channel,
            results=results,
            origin=op[3],
            reading=f"rdg_{n}",  # type: ignore[arg-type]
            clock_state=op[4],
            freshness=op[5],
        )  # type: ignore[arg-type]
    if kind == "fresh":
        origin: OriginMode = op[3]  # type: ignore[assignment]
        return world.freshness_input(
            op[2],
            since=now,
            origin=origin,  # type: ignore[arg-type]
            obs=h.observed(now, origin=origin),
            receive_reference=now,
        )
    if kind == "event":
        return world.event(f"evt_{n}", str(op[2]), now, origin=op[3])  # type: ignore[arg-type]
    if kind == "answer":
        world.answer_events = (world.event("evt_land", "landing", now - timedelta(hours=1)),)
        return AnswerInput(f"ans_{n}", "p1", "dizzy_standing", str(op[2]), now, now, op[3], "day", "09:00")  # type: ignore[arg-type]
    if kind == "fact":
        return StateFactInput("local_water_untested", op[2], world.environment.value, now, f"evt_{n}", op[3])  # type: ignore[arg-type]
    if kind == "link":
        return GroundLinkInput(op[2], now - timedelta(minutes=5), "adapter", "station_leo", bool(op[3]), now)  # type: ignore[arg-type]
    if kind == "supply":
        row = SupplyCoverRow("s1", "name", op[2], "part", int(op[3]), "rel_a", (), None, None, None, None)  # type: ignore[arg-type]
        return SupplyCoverInput("station_leo", "use", f"use_{n}", now, OriginMode.LIVE, (row,))
    if kind == "misc":
        ins = next((w for w in world.warnings.values() if w.insight_id), None)
        pick = op[2]
        if pick == "expire":
            return InsightExpiredInput(ins.insight_id if ins and ins.insight_id else "none", delta * 200)
        return (
            ActivationInput("release", "rel_a", "rel_a", "station_leo")
            if pick == "activate"
            else DeadlineInput("k", now)
        )
    if op[2] == "freeze":
        world.frozen = True
    elif op[2] == "thaw":
        world.frozen = False
    elif op[2] == "regress":
        world.clock_regressed = True
    else:
        world.clock_regressed = False
    return None


def scratch_world() -> h.World:
    raw = h.deployment_rules()
    import copy

    raw = copy.deepcopy(raw)
    for d in raw["definitions"]:  # type: ignore[attr-defined]
        if d["id"] == "ground_silence":
            d["condition"]["after_next_contact_seconds"] = 60
        if d["id"] == "supply_cover_low":
            d["condition"]["below_uses"] = 3
    return h.World(parse_rules_document(raw), h.release_documents(), environment=EnvironmentId.MARS_SURFACE)


@given(st.lists(OPS, min_size=1, max_size=14))
@h.PROP
@pytest.mark.trace("T-E24-012")
def test_t012_evaluate_is_total_deterministic_and_keeps_the_safety_invariants(
    ops: list[tuple[object, ...]],
) -> None:
    world = scratch_world()
    levels: dict[str, object] = {}
    for n, op in enumerate(ops):
        inp = build(world, op, n)
        if inp is None:
            continue
        view = world.view()
        decision = evaluate(inp, view)
        assert decision == evaluate(inp, view)  # deterministic
        if view.clears_frozen:
            assert not any(isinstance(s, Clear) for s in decision)  # no clear of any kind while pending
        for step in decision:
            if isinstance(step, Clear):
                target = world.warnings[step.warning_id]
                assert target.active and step.reason.value in {
                    "condition_false",
                    "clear_event",
                    "expired",
                    "superseded",
                }
            if isinstance(step, Raise) and step.pin.definition.raised_by is RaisedBy.ASSISTANT:
                assert step.pin.definition.level in ASSISTANT_LEVELS
        world.apply(decision)
        active = [w for w in world.warnings.values() if w.active]
        assert len({w.key for w in active}) == len(active)  # one active warning per (definition, scope key)
        for w in world.warnings.values():
            assert levels.setdefault(w.warning_id, w.level) == w.level  # a level never changes
            if w.scope is Scope.PERSON:
                assert (
                    w.origin_mode is world.mission_mode
                )  # a person warning never leaves the person's namespace
            if w.state is WarningState.CLEARED:
                assert w.clear_reason is not None
            if w.raised_by is RaisedBy.ASSISTANT:
                assert w.level in ASSISTANT_LEVELS
        if isinstance(inp, SensorEvaluatedInput) and not view.clears_frozen:
            group = [
                w.level.rank
                for w in active
                if w.pin.definition.condition.kind is ConditionKind.SENSOR_LIMIT
                and (w.sensor_type_id, w.channel_id) == (inp.sensor_type_id, inp.channel_id)
            ]
            assert not group or min(group) == max(
                group
            )  # none below the group's highest active level (INV-E24-05)


@given(st.lists(OPS, min_size=1, max_size=10))
@h.PROP
@pytest.mark.trace("T-E24-012", "INV-E24-16")
def test_a_frozen_engine_never_clears_whatever_the_inputs(ops: list[tuple[object, ...]]) -> None:
    world = scratch_world()
    world.frozen = True
    for n, op in enumerate(ops):
        if op[0] == "toggle":
            continue
        inp = build(world, op, n)
        if inp is None:
            continue
        decision = world.feed(inp)
        assert not any(isinstance(s, Clear) for s in decision)
    assert all(w.state in {WarningState.RAISED} for w in world.warnings.values())

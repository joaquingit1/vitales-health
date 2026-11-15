"""Sensor-limit raising, escalation, dwell and clearing evidence (E24 3.5 to 3.7).

T-E24-018 to 022, 024 to 029, 045, 046, 047, 049, 120 and the D1, D3, D14 regressions of the build plan.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain import clear_evidence
from crew_health.rules.domain.decisions import Clear, Raise, Refuse
from crew_health.rules.domain.definitions import RulesDocument
from crew_health.rules.domain.inputs import LimitRenderFacts
from crew_health.rules.domain.vocabulary import ClearReason, Level, OriginMode, WarningState

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "R35", "INV-E24-05", "INV-E24-07", "INV-E24-14")]

CROSSED = {"crossed": True, "clear_holds": False}
HOLDING = {"crossed": False, "clear_holds": True}


def co2_raise(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))


def holding(world: h.World, offset: int, *, limit: str = "co2_1h_act", **kw: object) -> None:
    world.now = h.T0 + timedelta(seconds=offset)
    world.feed(world.sensor(results=[h.result(limit, **HOLDING)], **kw))  # type: ignore[arg-type]


@pytest.mark.trace("T-E24-019")
def test_t019_two_oxygen_act_hazards_coexist_and_each_renders_its_own_limit(world: h.World) -> None:
    low = LimitRenderFacts("twa_1h", "one-hour time-weighted average", 3600, "120", "mm[Hg]", "mmHg", "127")
    high = LimitRenderFacts("latest", "latest value", None, "360", "mm[Hg]", "mmHg", "356")
    world.feed(
        world.sensor(
            sensor_type="o2",
            channel="ch_o2",
            results=[
                h.result("o2_low_act", render=low, value="120", **CROSSED),
                h.result("o2_high_act", render=high, value="360", **CROSSED),
            ],
            reading="rdg_o2_1",
        )
    )
    (a,) = world.by_definition("oxygen_low_act")
    (b,) = world.by_definition("oxygen_high_act")
    assert a.state is WarningState.RAISED and b.state is WarningState.RAISED
    assert a.level is Level.ACT and b.level is Level.ACT
    assert "one-hour time-weighted average" in a.what and "120" in a.what and "127" in a.what
    assert "latest value" in b.what and "360" in b.what and "356" in b.what
    assert a.what != b.what and a.limit_id == "o2_low_act" and b.limit_id == "o2_high_act"
    assert a.evidence[0].ref_id == b.evidence[0].ref_id == "rdg_o2_1"  # the same reading, each its own limit


@pytest.mark.trace("T-E24-020")
def test_t020_o2_below_the_emergency_limit_raises_emergency_and_supersedes_lower_ones(world: h.World) -> None:
    world.feed(world.sensor(sensor_type="o2", channel="ch_o2", results=[h.result("o2_low_watch", **CROSSED)]))
    world.advance(10)
    world.feed(
        world.sensor(
            sensor_type="o2",
            channel="ch_o2",
            results=[
                h.result("o2_low_watch", **CROSSED),
                h.result("o2_low_act", **CROSSED),
                h.result("o2_low_emergency", **CROSSED),
            ],
        )
    )
    (emergency,) = world.by_definition("oxygen_low_emergency")
    assert emergency.level is Level.EMERGENCY
    watch = world.by_definition("oxygen_low_watch", active_only=False)[0]
    assert watch.state is WarningState.CLEARED and watch.clear_reason is ClearReason.SUPERSEDED
    assert world.by_definition("oxygen_low_act") == []  # the lower crossing is not raised under a higher one
    assert emergency.previous_warning_id == watch.warning_id


@pytest.mark.trace("T-E24-018", "T-E24-021")
def test_t018_a_lower_crossing_under_a_higher_active_warning_raises_nothing_then_deescalates_linked(
    world: h.World,
) -> None:
    kw = {"sensor_type": "o2", "channel": "ch_o2"}
    world.feed(world.sensor(results=[h.result("o2_low_emergency", **CROSSED)], **kw))  # type: ignore[arg-type]
    (top,) = world.by_definition("oxygen_low_emergency")
    world.advance(10)
    world.feed(
        world.sensor(
            results=[h.result("o2_low_act", **CROSSED), h.result("o2_low_emergency", **CROSSED)], **kw
        )
    )  # type: ignore[arg-type]
    assert world.by_definition("oxygen_low_act") == []
    # The emergency clears by its own dwell while the act limit is still crossed: the lower one raises, linked.
    for offset in (70, 130, 190, 250, 310, 370, 430, 490, 550, 610, 670, 730):
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(
            world.sensor(
                results=[h.result("o2_low_act", **CROSSED), h.result("o2_low_emergency", **HOLDING)],
                **kw,  # type: ignore[arg-type]
            )
        )
        if world.warnings[top.warning_id].state is WarningState.CLEARED:
            break
    assert world.warnings[top.warning_id].clear_reason is ClearReason.CONDITION_FALSE
    (low,) = world.by_definition("oxygen_low_act")
    assert low.level is Level.ACT and low.previous_warning_id == top.warning_id
    assert world.warnings[top.warning_id].level is Level.EMERGENCY  # a level never changes


@pytest.mark.trace("T-E24-024")
def test_t024_a_stale_channel_raises_quiet_and_leaves_the_co2_warning_open(world: h.World) -> None:
    co2_raise(world)
    world.advance(400)
    world.feed(world.freshness_input("stale", since=world.now))
    assert world.by_definition("co2_above_limit")
    (quiet,) = world.by_definition("sensor_quiet")
    assert quiet.level is Level.WATCH and quiet.channel_id == "ch_co2"
    assert world.dwell == {}


@pytest.mark.trace("T-E24-029", "INV-E24-14")
def test_t029_never_reported_raises_nothing(world: h.World) -> None:
    assert world.feed(world.freshness_input("never_reported")) == ()


@pytest.mark.trace("T-E24-027", "T-E24-028", "INV-E24-14")
def test_t027_a_null_threshold_and_a_display_only_limit_raise_nothing(world: h.World) -> None:
    pending = world.limits["co2_1h_act"]
    from dataclasses import replace

    world.limits["co2_1h_act"] = replace(pending, value=None, status="threshold_pending")
    assert world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)])) == ()
    world.limits["co2_1h_act"] = replace(pending, role="display_only")
    assert world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)])) == ()
    # A pending radiation limit crossing is ignored: no definition names it and no number is invented.
    assert (
        world.feed(
            world.sensor(sensor_type="rad", channel="ch_rad", results=[h.result("rad_rate_act", **CROSSED)])
        )
        == ()
    )


@pytest.mark.trace("T-E24-025", "V56")
def test_t025_clear_holds_599_seconds_then_insufficient_restarts_the_dwell(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    for offset in (*range(60, 660, 60), 659):  # every minute, ending 599 seconds after the first evaluation
        holding(world, offset)
    assert world.warnings[w.warning_id].active and world.dwell[
        w.warning_id
    ].holding_since == h.T0 + timedelta(seconds=60)
    world.now = h.T0 + timedelta(seconds=700)
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=None, clear_holds=None)]))  # insufficient
    assert w.warning_id not in world.dwell and world.warnings[w.warning_id].active
    holding(world, 760)
    assert world.dwell[w.warning_id].holding_since == h.T0 + timedelta(seconds=760)
    for offset in range(820, 1361, 60):  # 600 seconds from 760
        holding(world, offset)
    cleared = world.warnings[w.warning_id]
    assert cleared.state is WarningState.CLEARED and cleared.clear_reason is ClearReason.CONDITION_FALSE


@pytest.mark.trace("T-E24-045")
def test_t045_sparse_readings_with_a_gap_above_the_stale_bound_reset_the_dwell(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    holding(world, 60)
    holding(world, 120)
    holding(world, 120 + 301)  # a gap above stale_after_seconds (300)
    assert world.dwell[w.warning_id].holding_since == h.T0 + timedelta(seconds=421)
    assert world.warnings[w.warning_id].active


@pytest.mark.trace("T-E24-046", "INV-E24-07")
def test_t046_the_same_evaluation_instant_never_advances_a_dwell_and_false_resets_it(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    holding(world, 60)
    before = world.dwell[w.warning_id]
    world.now = h.T0 + timedelta(seconds=61)
    world.feed(
        world.sensor(
            at=h.T0 + timedelta(seconds=60), results=[h.result("co2_1h_act", **HOLDING)], reading="rdg_late"
        )
    )
    assert world.dwell[w.warning_id] == before  # a late repeat at the same E advances nothing
    world.feed(
        world.sensor(
            at=h.T0 + timedelta(seconds=60),
            results=[h.result("co2_1h_act", crossed=False, clear_holds=False)],
            reading="rdg_late2",
        )
    )
    assert w.warning_id not in world.dwell  # false at the same E resets


@pytest.mark.trace("T-E24-120")
@pytest.mark.parametrize(
    "kwargs",
    [
        {"clock_state": "uncertain"},
        {"clock_state": "unknown"},
        {"freshness": "stale"},
    ],
)
def test_t120_untrusted_or_stale_evaluations_never_complete_a_dwell(
    world: h.World, kwargs: dict[str, str]
) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    for offset in range(60, 1500, 60):
        holding(world, offset, **kwargs)
    assert world.warnings[w.warning_id].active and w.warning_id not in world.dwell


@pytest.mark.trace("T-E24-120", "D3")
def test_t120_future_unknown_and_late_observations_never_clear(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    world.now = h.T0 + timedelta(seconds=100)
    inp_future = world.sensor(at=h.T0 + timedelta(seconds=400), results=[h.result("co2_1h_act", **HOLDING)])
    assert clear_evidence.eligible(inp_future, w, world.now).reason == "time_unknown"
    unknown = world.sensor(
        results=[h.result("co2_1h_act", **HOLDING)], obs=h.observed(world.now, state="unknown")
    )
    assert clear_evidence.eligible(unknown, w, world.now).reason == "time_unknown"
    old = world.sensor(at=h.T0 - timedelta(seconds=1000), results=[h.result("co2_1h_act", **HOLDING)])
    assert clear_evidence.eligible(old, w, world.now).reason == "not_fresh"
    before_raise = world.sensor(at=h.T0 - timedelta(seconds=5), results=[h.result("co2_1h_act", **HOLDING)])
    assert clear_evidence.eligible(before_raise, w, world.now).reason == "not_after_raise"


@pytest.mark.trace("T-E24-120", "D3")
def test_the_whole_uncertainty_interval_must_be_inside_freshness_and_after_the_raise(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    world.now = h.T0 + timedelta(seconds=100)
    at = h.T0 + timedelta(seconds=100)
    narrow = world.sensor(results=[h.result("co2_1h_act", **HOLDING)], obs=h.observed(at, uncertainty_ms=500))
    # The interval reaches 0.5 s into the future of `now`: not evidence yet.
    assert clear_evidence.eligible(narrow, w, world.now).reason == "time_unknown"
    earlier = h.T0 + timedelta(seconds=90)
    ok = world.sensor(
        at=earlier, results=[h.result("co2_1h_act", **HOLDING)], obs=h.observed(earlier, uncertainty_ms=500)
    )
    assert clear_evidence.eligible(ok, w, world.now).eligible
    straddles = h.T0 + timedelta(milliseconds=200)
    edge = world.sensor(
        at=straddles,
        results=[h.result("co2_1h_act", **HOLDING)],
        obs=h.observed(straddles, uncertainty_ms=500),
    )
    assert clear_evidence.eligible(edge, w, world.now).reason == "not_after_raise"


@pytest.mark.trace("T-E24-120", "D3")
def test_the_same_raw_time_in_a_different_clock_segment_breaks_continuity(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    holding(world, 60)
    first = world.dwell[w.warning_id]
    world.now = h.T0 + timedelta(seconds=120)
    e = world.now
    world.feed(
        world.sensor(at=e, results=[h.result("co2_1h_act", **HOLDING)], obs=h.observed(e, segment="seg2"))
    )
    assert world.dwell[w.warning_id].segment_key != first.segment_key
    assert world.dwell[w.warning_id].holding_since == e


@pytest.mark.trace("T-E24-120", "D14")
def test_replay_clears_on_data_time_but_only_replay_evidence_clears_a_replay_warning(world: h.World) -> None:
    from datetime import UTC, datetime

    data0 = datetime(2014, 10, 7, 15, 36, tzinfo=UTC)

    def replay(offset: int, **kw: object) -> None:
        e = data0 + timedelta(seconds=offset)
        world.advance(60)
        world.feed(
            world.sensor(
                at=e,
                origin=OriginMode.REPLAY,
                receive_reference=world.now,
                results=[h.result("co2_1h_act", **kw)],  # type: ignore[arg-type]
                reading=f"rdg_r_{offset}",
            )
        )

    replay(0, **CROSSED)
    (w,) = world.by_definition("co2_above_limit")
    assert w.origin_mode is OriginMode.REPLAY and w.evidence_time_label == "15:36, replay time"
    # A LIVE reading never clears a REPLAY warning, and a REPLAY one never clears a LIVE warning (D14).
    world.advance(60)
    live = world.sensor(results=[h.result("co2_1h_act", **HOLDING)])
    assert clear_evidence.eligible(live, w, world.now).reason == "origin_mismatch"
    for offset in range(60, 700, 60):
        replay(offset, **HOLDING)
    assert world.warnings[w.warning_id].state is WarningState.CLEARED
    assert world.warnings[w.warning_id].clear_reason is ClearReason.CONDITION_FALSE


@pytest.mark.trace("T-E24-047", "T-E24-120", "D14")
def test_a_replay_that_does_not_advance_and_a_receive_reference_that_is_stale_never_clear(
    world: h.World,
) -> None:
    from datetime import UTC, datetime

    data0 = datetime(2014, 10, 7, 15, 36, tzinfo=UTC)
    world.feed(world.sensor(at=data0, origin=OriginMode.REPLAY, results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    world.advance(10)
    no_ref = world.sensor(
        at=data0 + timedelta(seconds=60),
        origin=OriginMode.REPLAY,
        results=[h.result("co2_1h_act", **HOLDING)],
    )
    from dataclasses import replace

    assert (
        clear_evidence.eligible(replace(no_ref, receive_reference=None), w, world.now).reason
        == "no_receive_reference"
    )
    old_ref = replace(no_ref, receive_reference=world.now - timedelta(seconds=1000))
    assert clear_evidence.eligible(old_ref, w, world.now).reason == "not_fresh"
    not_later = replace(
        no_ref, observed=h.observed(data0, origin=OriginMode.REPLAY), receive_reference=world.now
    )
    assert clear_evidence.eligible(not_later, w, world.now).reason == "not_after_raise"


@pytest.mark.trace("T-E24-049")
def test_t049_dense_fast_replay_clears_on_observed_time_not_wall_time(world: h.World) -> None:
    from datetime import UTC, datetime

    data0 = datetime(2014, 10, 7, 15, 36, tzinfo=UTC)
    world.feed(world.sensor(at=data0, origin=OriginMode.REPLAY, results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    for i in range(1, 80):  # one wall-clock second apart, ten data seconds apart
        world.advance(1)
        world.feed(
            world.sensor(
                at=data0 + timedelta(seconds=10 * i),
                origin=OriginMode.REPLAY,
                results=[h.result("co2_1h_act", **HOLDING)],
                reading=f"rdg_fast_{i}",
            )
        )
        if world.warnings[w.warning_id].state is WarningState.CLEARED:
            break
    assert world.warnings[w.warning_id].state is WarningState.CLEARED
    assert (world.now - h.T0).total_seconds() < 100  # far less wall time than the 600 data seconds


# --- D1: the pinned rule decides the clear -----------------------------------------------------------


@pytest.mark.trace("T-E24-014", "T-E24-015", "T-E24-126", "INV-E24-15", "D1")
def test_d1_a_result_evaluated_under_another_limit_never_clears_the_pinned_warning(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    assert w.pin.limit is not None
    for offset in range(60, 1500, 60):
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(world.sensor(results=[h.result("co2_1h_act", fingerprint="f" * 64, **HOLDING)]))
    assert world.warnings[w.warning_id].active  # unevaluable under the pin: waits for the pin or an officer


@pytest.mark.trace("T-E24-014", "D1")
def test_d1_a_result_under_the_pinned_fingerprint_clears_even_after_a_release_change(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    assert w.pin.limit is not None
    world.release_id = "rel_b"  # a new release activates; the old warning keeps its pin
    for offset in range(60, 700, 60):
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(
            world.sensor(
                release="rel_b",
                results=[h.result("co2_1h_act", fingerprint=w.pin.limit.limit_fingerprint, **HOLDING)],
            )
        )
    assert world.warnings[w.warning_id].state is WarningState.CLEARED


@pytest.mark.trace("T-E24-126", "D1")
def test_d1_without_a_pinned_fingerprint_only_the_warnings_own_release_may_clear(world: h.World) -> None:
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    world.release_id = "rel_b"
    world.now = h.T0 + timedelta(seconds=60)
    new_release = world.sensor(release="rel_b", results=[h.result("co2_1h_act", **HOLDING)])
    assert clear_evidence.eligible(new_release, w, world.now).reason == "pin_unavailable"


@pytest.mark.trace("T-E24-120", "INV-E24-16")
def test_a_pending_evaluation_freezes_every_clear_and_every_supersession_but_not_a_raise(
    world: h.World,
) -> None:
    kw = {"sensor_type": "o2", "channel": "ch_o2"}
    world.feed(world.sensor(results=[h.result("o2_low_watch", **CROSSED)], **kw))  # type: ignore[arg-type]
    (watch,) = world.by_definition("oxygen_low_watch")
    world.frozen = True
    world.advance(10)
    world.feed(
        world.sensor(
            results=[h.result("o2_low_watch", **CROSSED), h.result("o2_low_emergency", **CROSSED)], **kw
        )
    )  # type: ignore[arg-type]
    assert world.warnings[watch.warning_id].active  # not superseded while frozen
    assert world.by_definition("oxygen_low_emergency")  # raises continue
    for offset in range(100, 1500, 60):
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(
            world.sensor(
                results=[h.result("o2_low_watch", **HOLDING), h.result("o2_low_emergency", **HOLDING)], **kw
            )
        )  # type: ignore[arg-type]
    assert all(w.active for w in world.warnings.values())
    assert world.dwell == {}
    world.frozen = False
    world.advance(60)
    world.feed(
        world.sensor(
            results=[h.result("o2_low_watch", **CROSSED), h.result("o2_low_emergency", **CROSSED)], **kw
        )
    )  # type: ignore[arg-type]
    assert world.warnings[watch.warning_id].clear_reason is ClearReason.SUPERSEDED  # cleaned up once unfrozen


def test_a_stale_release_input_raises_nothing_and_is_refused(world: h.World) -> None:
    decision = world.feed(world.sensor(release="rel_other", results=[h.result("co2_1h_act", **CROSSED)]))
    assert [type(s) for s in decision] == [Refuse]
    assert decision[0].code == "stale_release"  # type: ignore[union-attr]
    assert not world.warnings


@pytest.mark.trace("T-E24-007")
def test_two_identical_inputs_give_one_active_warning_and_a_spent_trigger_never_raises_again(
    world: h.World,
) -> None:
    inp = world.sensor(results=[h.result("co2_1h_act", **CROSSED)], reading="rdg_fixed")
    world.feed(inp)
    assert world.feed(inp) == ()
    (w,) = world.by_definition("co2_above_limit")
    world.apply((Clear(w.warning_id, ClearReason.CONDITION_FALSE),))
    again = world.feed(inp)
    assert [type(s) for s in again] == [Refuse] and again[0].code == "trigger_spent"  # type: ignore[union-attr]
    assert not world.by_definition("co2_above_limit")


def test_a_raise_carries_its_own_pin_and_the_sources_of_its_limit(world: h.World) -> None:
    decision = world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (r,) = [s for s in decision if isinstance(s, Raise)]
    assert r.pin.limit is not None and r.pin.limit.dwell_seconds == 600
    assert r.pin.limit.limit_fingerprint == world.limits["co2_1h_act"].limit_fingerprint


# --- properties --------------------------------------------------------------------------------------

STEP = st.tuples(
    st.integers(min_value=1, max_value=400),
    st.sampled_from(["holding", "false", "none", "uncertain", "stale", "gap_future"]),
)


@given(st.lists(STEP, min_size=1, max_size=40))
@h.PROP
@pytest.mark.trace("T-E24-026", "INV-E24-07")
def test_t026_a_warning_never_clears_before_a_full_eligible_dwell(steps: list[tuple[int, str]]) -> None:
    """Any stale, insufficient or ineligible step inside the dwell prevents a clear before 600 eligible seconds."""
    from crew_health.rules.domain.definitions import parse_rules_document

    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    co2_raise(world)
    (w,) = world.by_definition("co2_above_limit")
    offset = 0
    run_start: int | None = None
    last_eligible: int | None = None
    for delta, kind in steps:
        offset += delta
        world.now = h.T0 + timedelta(seconds=offset)
        e = world.now
        kwargs: dict[str, object] = {}
        flags = HOLDING
        if kind == "false":
            flags = {"crossed": False, "clear_holds": False}
        elif kind == "none":
            flags = {"crossed": None, "clear_holds": None}
        elif kind == "uncertain":
            kwargs["clock_state"] = "uncertain"
        elif kind == "stale":
            kwargs["freshness"] = "stale"
        elif kind == "gap_future":
            kwargs["at"] = e + timedelta(seconds=30)
        world.feed(world.sensor(results=[h.result("co2_1h_act", **flags)], **kwargs))  # type: ignore[arg-type]
        eligible = kind == "holding"
        if eligible:
            if last_eligible is None or offset - last_eligible > 300 or run_start is None:
                run_start = offset
            last_eligible = offset
        else:
            run_start = None
            last_eligible = None
        if world.warnings[w.warning_id].state is WarningState.CLEARED:
            assert run_start is not None and offset - run_start >= 600
            break
        assert (w.warning_id in world.dwell) == (eligible and run_start is not None)


def test_clear_decisions_are_clears_with_reasons_only_from_the_engine_set(world: h.World) -> None:
    co2_raise(world)
    for offset in range(60, 700, 60):
        world.now = h.T0 + timedelta(seconds=offset)
        decision = world.feed(world.sensor(results=[h.result("co2_1h_act", **HOLDING)]))
        for step in decision:
            if isinstance(step, Clear):
                assert step.reason is ClearReason.CONDITION_FALSE


def test_example_document_is_unaffected_by_the_deployment_edits(
    example_doc: RulesDocument, release_docs: object
) -> None:
    """The example storm is `act`; only the deployment document raises it at emergency (D2)."""
    assert example_doc.by_id()["radiation_storm_shelter"].level is Level.ACT

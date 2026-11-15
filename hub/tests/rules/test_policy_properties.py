"""Properties of ordering, headline, dwell, occurrences and clearing evidence (1,000 examples each).

T-E24-026, 043, 118, 120 and INV-E24-07, INV-E24-17: the policies hold for every generated case, not
only for the worked examples.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain import clear_evidence, dwell
from crew_health.rules.domain.definitions import HeadlineRule, parse_rules_document
from crew_health.rules.domain.headline import headline
from crew_health.rules.domain.ordering import after, order_key, ordered
from crew_health.rules.domain.vocabulary import ClearReason, Level, OriginMode, WarningState
from crew_health.rules.domain.warning import HeadlineFields, Warning

from . import rules_harness as h

pytestmark = [pytest.mark.E24, pytest.mark.trace("T-E24-012", "INV-E24-07", "INV-E24-17")]


def base_warning() -> Warning:
    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (w,) = world.by_definition("co2_above_limit")
    return w


BASE = base_warning()

SPECS = st.lists(
    st.tuples(st.integers(0, 4), st.integers(0, 6), st.booleans(), st.sampled_from(["raised", "cleared"])),
    max_size=14,
)


def build(specs: list[tuple[int, int, bool, str]]) -> list[Warning]:
    items: list[Warning] = []
    for i, (rank, minutes, solo, state) in enumerate(specs):
        w = replace(
            BASE,
            warning_id=f"wrn_{i:03d}",
            raised_at=h.T0 + timedelta(minutes=minutes),
            headline=HeadlineFields(f"fragment {i}", rank, solo),
        )
        if state == "cleared":
            w = replace(
                w,
                state=WarningState.CLEARED,
                cleared_at=w.raised_at,
                clear_reason=ClearReason.CONDITION_FALSE,
            )
        items.append(w)
    return items


@given(SPECS, st.integers(1, 5))
@h.PROP
@pytest.mark.trace("T-E24-043")
def test_t043_list_order_is_a_total_order_and_pages_partition_it(
    specs: list[tuple[int, int, bool, str]], page: int
) -> None:
    items = build(specs)
    listed = ordered(items)
    keys = [order_key(w) for w in listed]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert ordered(reversed(items)) == listed  # independent of input order
    walked: list[Warning] = []
    cursor = None
    while True:
        chunk = after(items, cursor, page)
        if not chunk:
            break
        assert len(chunk) <= page
        walked.extend(chunk)
        cursor = order_key(chunk[-1])
    assert walked == list(listed)  # every item exactly once


@given(SPECS, st.integers(1, 4))
@h.PROP
@pytest.mark.trace("T-E24-042")
def test_the_headline_uses_only_active_warnings_in_rank_order_within_its_bound(
    specs: list[tuple[int, int, bool, str]], bound: int
) -> None:
    rule = HeadlineRule(bound, " / ", "nothing")
    items = build(specs)
    view = headline(items, rule)
    active = [w for w in ordered(items) if w.active]
    if not active:
        assert view.text == "nothing" and view.warning_ids == ()
        return
    assert set(view.warning_ids) <= {w.warning_id for w in active}
    if active[0].headline.solo:
        assert view.warning_ids == (active[0].warning_id,) and view.text == active[0].headline.fragment
    else:
        assert 1 <= len(view.warning_ids) <= bound
        assert all(not next(w for w in active if w.warning_id == i).headline.solo for i in view.warning_ids)
        assert view.text[:1] == view.text[:1].upper()


@given(
    st.lists(st.tuples(st.integers(-50, 400), st.booleans()), min_size=1, max_size=40),
    st.integers(0, 900),
    st.integers(1, 400),
)
@h.PROP
@pytest.mark.trace("T-E24-026", "T-E24-046")
def test_dwell_never_clears_early_never_moves_backwards_and_same_instant_advances_nothing(
    steps: list[tuple[int, bool]], dwell_seconds: int, gap_bound: int
) -> None:
    marker: dwell.DwellMarker | None = None
    clock = 0
    for delta, eligible in steps:
        clock += delta
        e = h.T0 + timedelta(seconds=clock)
        outcome = dwell.step(
            marker, warning_id="w", channel_id="c", e=e if eligible else None, segment_key="s",
            eligible=eligible, dwell_seconds=dwell_seconds, gap_bound_seconds=gap_bound, fingerprint="f",
        )  # fmt: skip
        if outcome.clear:
            assert outcome.marker is not None and eligible
            assert (e - outcome.marker.holding_since).total_seconds() >= dwell_seconds
        if outcome.action == "put":
            assert (
                outcome.marker is not None and outcome.marker.holding_since <= outcome.marker.last_window_end
            )
            if marker is not None and marker.holding_since == outcome.marker.holding_since:
                assert (
                    outcome.marker.last_window_end > marker.last_window_end
                )  # only a later instant advances it
                assert (outcome.marker.last_window_end - marker.last_window_end).total_seconds() <= gap_bound
            marker = outcome.marker
        elif outcome.action == "delete":
            marker = None
        if not eligible:
            assert marker is None and not outcome.clear  # an ineligible evaluation always breaks continuity
        if outcome.clear:
            marker = None


# --- clearing evidence: each aspect alone breaks eligibility ---------------------------------------------


def eligible_world() -> tuple[h.World, Warning]:
    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (w,) = world.by_definition("co2_above_limit")
    world.now = h.T0 + timedelta(seconds=100)
    return world, w


@given(
    st.sampled_from(["trusted", "uncertain", "unknown"]),
    st.sampled_from(["current", "stale"]),
    st.sampled_from(list(OriginMode)),
    st.integers(-400, 400),
    st.integers(0, 3000),
    st.sampled_from([True, False, None]),
    st.sampled_from([True, False, None]),
    st.sampled_from(["pinned", "other", "none"]),
    st.sampled_from(["known", "unknown", "future"]),
)
@h.PROP
@pytest.mark.trace("T-E24-120", "INV-E24-07", "D3")
def test_t120_eligibility_implies_every_condition_of_d3(
    clock_state: Literal["trusted", "uncertain", "unknown"], freshness: Literal["current", "stale"], origin: OriginMode, offset: int, uncertainty_ms: int,
    crossed: bool | None, holds: bool | None, fingerprint: str, time_state: Literal["known", "unknown", "future"],
) -> None:  # fmt: skip
    world, w = eligible_world()
    assert w.pin.limit is not None
    fp = {"pinned": w.pin.limit.limit_fingerprint, "other": "0" * 64, "none": None}[fingerprint]
    when: datetime = h.T0 + timedelta(seconds=100 + offset)
    inp = world.sensor(
        at=when,
        origin=origin,
        clock_state=clock_state,
        freshness=freshness,
        results=[h.result("co2_1h_act", crossed=crossed, clear_holds=holds, fingerprint=fp)],
        obs=h.observed(
            when,
            origin=origin,
            uncertainty_ms=uncertainty_ms,
            state=time_state if origin is not OriginMode.REPLAY else "replay",
        ),
        receive_reference=world.now,
    )
    verdict = clear_evidence.eligible(inp, w, world.now)
    if not verdict.eligible:
        return
    assert origin is w.origin_mode  # evidence of another origin never clears
    assert clock_state == "trusted" and freshness == "current"
    assert holds is True and crossed is not True and fp in {w.pin.limit.limit_fingerprint, None}
    if origin is OriginMode.REPLAY:
        assert when > w.evidence_time
    else:
        assert time_state == "known"
        low, high = (
            when - timedelta(milliseconds=uncertainty_ms),
            when + timedelta(milliseconds=uncertainty_ms),
        )
        assert low > w.raised_at and high <= world.now and low >= world.now - timedelta(seconds=300)
    if fp is None:
        assert inp.config_release_id == w.config_release_id


# --- occurrences: the outcome of any delivery order ----------------------------------------------------

EVENTS = st.lists(
    st.tuples(st.sampled_from(["alert", "clear"]), st.integers(0, 12), st.booleans()),
    min_size=1,
    max_size=12,
)


@given(EVENTS)
@h.PROP
@pytest.mark.trace("T-E24-118", "T-E24-119", "INV-E24-17", "H13")
def test_t118_a_clear_event_clears_only_when_every_outstanding_occurrence_is_resolved_by_a_later_clear(
    events: list[tuple[str, int, bool]],
) -> None:
    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    clear_times: dict[str, datetime] = {}
    for index, (kind, minute, duplicate) in enumerate(events):
        when = h.T0 + timedelta(minutes=minute)
        event_id = f"evt_{index}"
        event_type = "space_weather_alert" if kind == "alert" else "space_weather_all_clear"
        world.now = max(world.now, when)
        if kind == "clear":
            clear_times[event_id] = when
        for _ in range(2 if duplicate else 1):  # a duplicate delivery changes nothing
            before = (dict(world.warnings), {k: tuple(v) for k, v in world.occurrences.items()})
            decision = world.feed(world.event(event_id, event_type, when))
            if _ == 1:
                assert decision == () or all(type(s).__name__ == "Refuse" for s in decision)
                assert before == (dict(world.warnings), {k: tuple(v) for k, v in world.occurrences.items()})
        for w in world.warnings.values():
            occurrences = world.occurrences.get(w.warning_id, [])
            if w.active:
                assert w.state in {WarningState.RAISED, WarningState.ACKNOWLEDGED}
                assert any(
                    o.resolved_by_event_id is None for o in occurrences
                )  # active implies an outstanding occurrence
            if w.clear_reason is ClearReason.CLEAR_EVENT:
                for o in occurrences:
                    assert o.resolved_by_event_id is not None
                    assert (
                        clear_times[o.resolved_by_event_id] > o.occurred_at
                    )  # resolved only by a strictly later clear
    assert Level.EMERGENCY.rank == 3

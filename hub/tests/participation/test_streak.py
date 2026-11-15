"""Streak derivation (CONTRACTS 8 rules 3 to 9; E22 3.2). Golden cases are the CONTRACTS 20 vector and the package
examples, checked independently of the derivation; properties compare against a brute force reference."""

from __future__ import annotations

import random
from dataclasses import fields
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.participation.domain.derivation import (
    MAX_VIEW_DAYS,
    CompletionFact,
    derive_streak,
    event_cursor,
    reached_attributions,
    resolve_first_day,
)
from crew_health.participation.domain.errors import InvalidBasis, ViewBoundExceeded
from crew_health.participation.domain.types import StreakView

from .harness import PROP, example_value, fact, facts_for, rules, validator

pytestmark = pytest.mark.E22
AS_OF = datetime(2026, 11, 13, 9, 10, 16, tzinfo=UTC)


def view(days: list[int], today: int, *, first: int | None = 1, patient: str = "mara") -> StreakView:
    return derive_streak(
        patient_id=patient,
        mission_id="demo-mission",
        origin_mode="SIM",
        facts=facts_for(days, patient=patient),
        today=today,
        roster_first_day=first,
        as_of=AS_OF,
        rules=rules(),
        clock_version="clock-1",
        cursor="cursor",
    )


def wire(v: StreakView) -> dict[str, object]:
    return {
        "patient_id": v.patient_id,
        "mission_id": v.mission_id,
        "origin_mode": v.origin_mode,
        "as_of": "2026-11-13T09:10:16Z",
        "current_mission_day": v.current_mission_day,
        "today": v.today,
        "current_streak_days": v.current_streak_days,
        "longest_streak_days": v.longest_streak_days,
        "completed_days_total": v.completed_days_total,
        "first_mission_day": v.first_mission_day,
        "days": [{"mission_day": d.mission_day, "status": d.status} for d in v.days],
        "derivation_version": v.derivation_version,
        "mission_clock_version": v.mission_clock_version,
        "event_cursor": v.event_cursor,
    }


def brute_force(checked: set[int], first: int, today: int) -> tuple[int, int, int]:
    """Independent reference: walk the days one by one."""
    days = [d for d in range(first, today + 1)]
    best = run = 0
    for d in days:
        run = run + 1 if d in checked else 0
        best = max(best, run)
    current = 0
    d = today if today in checked else today - 1
    while d >= first and d in checked:
        current += 1
        d -= 1
    return current, best, len([d for d in days if d in checked])


@pytest.mark.trace("T-E22-002", "D03", "R31")
def test_contracts_vector_days_1_to_18_then_submit_on_day_19() -> None:
    before = view(list(range(1, 19)), 19)
    after = view(list(range(1, 20)), 19)
    assert before.today == "due"
    assert (before.current_streak_days, before.longest_streak_days, before.completed_days_total) == (
        18,
        18,
        18,
    )
    assert after.today == "checked"
    assert (after.current_streak_days, after.longest_streak_days, after.completed_days_total) == (19, 19, 19)


@pytest.mark.trace("T-E22-002", "T-E22-003", "D03")
def test_views_equal_the_package_examples_apart_from_as_of() -> None:
    for name, days in (("streak-after-submit", range(1, 20)), ("streak-station", range(1, 19))):
        expected = example_value(f"participation/{name}.json")
        got = wire(view(list(days), 19))
        got["event_cursor"] = expected["event_cursor"]
        assert got == {**expected, "as_of": "2026-11-13T09:10:16Z"}
        assert list(validator("streak-view.schema.json").iter_errors(got)) == []


@pytest.mark.trace("T-E22-010", "INV-E22-04", "R33")
def test_today_is_never_missed_and_the_boundary_makes_yesterday_missed() -> None:
    on_day_5 = view([1, 2, 3, 4], 5)
    assert on_day_5.days[-1].status == "due"
    assert on_day_5.today == "due"
    assert on_day_5.current_streak_days == 4
    on_day_6 = view([1, 2, 3, 4], 6)
    assert [d.status for d in on_day_6.days][-2:] == ["missed", "due"]
    assert on_day_6.current_streak_days == 0
    assert all(d.status != "missed" for d in on_day_6.days if d.mission_day == 6)


@pytest.mark.trace("T-E22-011", "V44", "R33", "INV-E22-04")
def test_missed_day_then_a_check_keeps_longest_and_restarts_current() -> None:
    v = view([1, 2, 3, 4, 5, 6, 8], 8)
    statuses = {d.mission_day: d.status for d in v.days}
    assert statuses[7] == "missed"
    assert statuses[8] == "checked"
    assert (v.current_streak_days, v.longest_streak_days, v.completed_days_total) == (1, 6, 7)


@pytest.mark.trace("T-E22-012", "V46", "INV-E22-03")
def test_several_completions_on_one_day_count_once() -> None:
    two = [fact(3, ref="dck_a", hours=1), fact(3, ref="dck_b", hours=23)]
    v = derive_streak(
        patient_id="mara",
        mission_id="demo-mission",
        origin_mode="SIM",
        facts=two,
        today=3,
        roster_first_day=3,
        as_of=AS_OF,
        rules=rules(),
        clock_version="clock-1",
        cursor="c",
    )
    assert (v.completed_days_total, v.current_streak_days, v.longest_streak_days) == (1, 1, 1)


@pytest.mark.trace("T-E22-017", "R31", "R33")
@PROP
@given(
    checked=st.sets(st.integers(min_value=-5, max_value=40), max_size=30),
    first=st.integers(min_value=-5, max_value=10),
    span=st.integers(min_value=0, max_value=40),
)
def test_streak_longest_and_total_agree_with_brute_force(checked: set[int], first: int, span: int) -> None:
    today = first + span
    v = view(sorted(checked), today, first=first)
    current, longest, total = brute_force(checked, first, today)
    assert (v.current_streak_days, v.longest_streak_days, v.completed_days_total) == (current, longest, total)
    assert v.today == ("checked" if today in checked else "due")
    assert v.current_streak_days <= v.longest_streak_days <= v.completed_days_total <= span + 1
    assert [d.mission_day for d in v.days] == list(range(first, today + 1))
    assert sum(d.status == "checked" for d in v.days) == total


@pytest.mark.trace("T-E22-034", "T-E22-036", "INV-E22-09")
@PROP
@given(
    days=st.lists(st.integers(min_value=1, max_value=30), max_size=40),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_arrival_order_never_changes_the_view(days: list[int], seed: int) -> None:
    facts = [fact(d, ref=f"dck_{i}") for i, d in enumerate(days)]
    shuffled = list(facts)
    random.Random(seed).shuffle(shuffled)

    def derive(fs: list[CompletionFact]) -> StreakView:
        return derive_streak(
            patient_id="mara",
            mission_id="demo-mission",
            origin_mode="SIM",
            facts=fs,
            today=30,
            roster_first_day=1,
            as_of=AS_OF,
            rules=rules(),
            clock_version="clock-1",
            cursor="c",
        )

    assert derive(facts) == derive(shuffled)


@pytest.mark.trace("T-E22-042", "V46")
def test_first_day_rules_for_guest_future_start_and_preflight() -> None:
    f5, f7 = fact(5), fact(7)
    assert resolve_first_day(None, [], 9) == 9
    assert resolve_first_day(None, [f7, f5], 9) == 5
    assert resolve_first_day(3, [f5], 9) == 3
    # a guest's first read shows today due with zeros
    first_read = view([], 9, first=None)
    assert (first_read.today, first_read.first_mission_day, first_read.completed_days_total) == ("due", 9, 0)
    assert [(d.mission_day, d.status) for d in first_read.days] == [(9, "due")]
    # after the first check
    after = view([9], 9, first=None)
    assert (after.today, after.current_streak_days) == ("checked", 1)
    # a roster start in the future: no days, today due, zeros, the completion ignored until eligible
    future = view([6], 5, first=8)
    assert (future.days, future.today, future.completed_days_total, future.current_streak_days) == (
        (),
        "due",
        0,
        0,
    )
    assert future.first_mission_day == 8
    # pre-flight days are ordinary days
    pre = view([-2, -1, 0, 1], 1, first=-2)
    assert (pre.current_streak_days, pre.completed_days_total) == (4, 4)


@pytest.mark.trace("T-E22-042")
def test_completion_after_today_is_ignored_and_one_filter_feeds_every_count() -> None:
    v = view([3, 4, 9], 5, first=1)  # day 9 lies after today (a backward clock correction)
    assert v.completed_days_total == 2
    assert v.longest_streak_days == 2
    assert sum(d.status == "checked" for d in v.days) == 2
    # before the first eligible day
    early = view([1, 2, 8], 9, first=3)
    assert early.completed_days_total == 1
    assert early.days[0].mission_day == 3


@pytest.mark.trace("T-E22-042")
def test_view_bound_is_asserted_and_never_truncates() -> None:
    ok = view([], MAX_VIEW_DAYS, first=1)
    assert len(ok.days) == MAX_VIEW_DAYS
    with pytest.raises(ViewBoundExceeded):
        view([], MAX_VIEW_DAYS + 1, first=1)


@pytest.mark.trace("INV-E22-05", "T-E22-050")
def test_a_completion_of_another_person_is_refused() -> None:
    with pytest.raises(InvalidBasis):
        derive_streak(
            patient_id="mara",
            mission_id="demo-mission",
            origin_mode="SIM",
            facts=[fact(1, patient="tomas")],
            today=1,
            roster_first_day=1,
            as_of=AS_OF,
            rules=rules(),
            clock_version="clock-1",
            cursor="c",
        )


@pytest.mark.trace("T-E22-016")
def test_patch_attribution_is_the_kth_eligible_day_with_least_occurred_at_then_id() -> None:
    early_b = fact(7, ref="dck_b", hours=1)
    late_a = fact(7, ref="dck_a", hours=20)
    others = [fact(d) for d in range(1, 7)]
    got = reached_attributions([late_a, early_b, *others], 1, 7, [7, 14])
    assert set(got) == {7}
    assert (got[7].completion_id, got[7].mission_day) == (early_b.completion_id, 7)
    # equal instants: the least completion id wins
    twin_1, twin_2 = fact(7, ref="dck_x", hours=5), fact(7, ref="dck_y", hours=5)
    tie = reached_attributions([twin_1, twin_2, *others], 1, 7, [7])
    assert tie[7].completion_id == min(twin_1.completion_id, twin_2.completion_id)
    # the k-th eligible day, not day k: a gap moves the attribution later
    gapped = reached_attributions([fact(d) for d in (1, 2, 3, 4, 5, 6, 8)], 1, 9, [7])
    assert gapped[7].mission_day == 8
    # days outside the eligible range do not count and a non positive threshold reaches nothing
    assert reached_attributions([fact(d) for d in range(1, 8)], 3, 7, [7, 0]) == {}


@pytest.mark.trace("T-E22-036", "INV-E22-09")
@PROP
@given(
    pairs=st.lists(st.tuples(st.uuids().map(str), st.booleans()), max_size=20, unique_by=lambda p: p[0]),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_event_cursor_is_order_independent_and_sensitive(pairs: list[tuple[str, bool]], seed: int) -> None:
    shuffled = list(pairs)
    random.Random(seed).shuffle(shuffled)
    assert event_cursor(pairs, "h1") == event_cursor(shuffled, "h1")
    assert len(event_cursor(pairs, "h1")) == 32
    assert event_cursor(pairs, "h1") != event_cursor(pairs, "h2")
    if pairs:
        flipped = [(pairs[0][0], not pairs[0][1]), *pairs[1:]]
        assert event_cursor(flipped, "h1") != event_cursor(pairs, "h1")


@pytest.mark.trace("T-E22-001", "T-E22-004", "INV-E22-01", "V41", "V48")
def test_the_derivation_inputs_and_outputs_carry_no_value_field() -> None:
    assert [f.name for f in fields(CompletionFact)] == [
        "completion_id",
        "patient_id",
        "mission_day",
        "occurred_at",
    ]
    forbidden = ("value", "presence", "quality", "outcome", "symptom", "journal", "answer", "state")
    names = [f.name for f in fields(StreakView)]
    assert not [n for n in names if any(word in n for word in forbidden)]

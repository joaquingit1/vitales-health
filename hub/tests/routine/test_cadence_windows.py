from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.routine.domain.cadence import (
    CadenceCandidate,
    effective_cadence,
    next_periodic_day,
    period_days,
)
from crew_health.routine.domain.errors import ConfigInvalid, InvalidBody
from crew_health.routine.domain.release import Cadence
from crew_health.routine.domain.rendering import cadence_text, capitalize_first, end_text_key, hhmm, render
from crew_health.routine.domain.vocabulary import CadenceWord, OccurrenceState
from crew_health.routine.domain.windows import (
    after_walk_window,
    before_walk_window,
    policy_window,
    slot_window,
    trigger_window,
)

from .support import NOW, release


def cadences() -> tuple[Cadence, ...]:
    r = release()
    return tuple(
        c
        for item in r.items
        for c in (
            item.default_cadence,
            *item.by_environment.values(),
            *(t.new_cadence for t in item.triggers),
        )
    )


@pytest.mark.parametrize(
    "word,expected",
    [
        ("continuous", 1),
        ("daily", 1),
        ("every_n_days", None),
        ("weekly", 7),
        ("monthly", 30),
        ("per_eva", None),
        ("after_event", None),
        ("mission_phase", None),
        ("as_needed", None),
    ],
)
@pytest.mark.trace("T-E25-021")
def test_every_cadence_word_has_its_contract_period(word: CadenceWord, expected: int | None) -> None:
    c = next(c for c in cadences() if c.word == word)
    # CONTRACTS 15 cadence table: daily/continuous 1, every_n_days n, weekly/monthly calendar.
    assert period_days(c, release().calendar) == (c.n if word == "every_n_days" else expected)


@pytest.mark.parametrize(
    "anchor,last,today,shown,expected",
    [
        (1, None, 1, False, 1),
        (1, None, 10, False, 10),
        (1, 10, 11, False, 17),
        (8, 4, 8, False, 8),
        (1, None, 10, True, 11),
    ],
)
@pytest.mark.trace("T-E25-021", "INV-E25-14")
def test_next_periodic_day_respects_anchor_history_and_shown_days(
    anchor: int, last: int | None, today: int, shown: bool, expected: int
) -> None:
    # CONTRACTS 15: first on anchor or today; n days after last ended since anchor.
    assert (
        next_periodic_day(
            period=7, anchor_day=anchor, last_final_day=last, today=today, today_has_periodic=shown
        )
        == expected
    )


@given(st.integers(1, 40), st.lists(st.integers(1, 60), max_size=10))
@pytest.mark.trace("T-E25-009", "INV-E25-04")
def test_effective_period_is_shortest_and_never_exceeds_base(base_period: int, periods: list[int]) -> None:
    r = release()
    base = replace(next(c for c in cadences() if c.word == "every_n_days"), n=base_period)
    candidates = tuple(
        CadenceCandidate(
            str(i),
            replace(base, n=p),
            NOW + timedelta(minutes=i),
            r.items[0].cadence_basis,
            r.items[0].source_ids,
        )
        for i, p in enumerate(periods)
    )
    result = effective_cadence(base, r.items[0].cadence_basis, r.items[0].source_ids, candidates, r.calendar)
    assert period_days(result.cadence, r.calendar) == min([base_period, *periods])


@pytest.mark.trace("T-E25-009")
def test_effective_cadence_ties_use_latest_start_then_larger_id_and_events_are_additive() -> None:
    r = release()
    base = next(c for c in cadences() if c.word == "weekly")
    daily = next(c for c in cadences() if c.word == "daily")
    candidates = tuple(
        CadenceCandidate(i, daily, NOW + timedelta(minutes=m), r.items[0].cadence_basis, ())
        for i, m in [("z", 0), ("a", 1), ("b", 1)]
    )
    assert effective_cadence(base, "", (), candidates, r.calendar).governing_override_id == "b"
    event = next(c for c in cadences() if c.word == "after_event")
    assert (
        effective_cadence(base, "", (), (replace(candidates[0], cadence=event),), r.calendar).cadence == base
    )
    assert effective_cadence(event, "", (), candidates, r.calendar).cadence == daily


@pytest.mark.trace("T-E25-035")
def test_windows_use_configured_time_and_null_window_ends_at_day_end() -> None:
    r = release()
    end = NOW + timedelta(days=1)
    policy = r.policy(r.items[0].item_id)
    assert policy is not None and policy.preferred_minute is not None
    window = policy_window(policy, NOW, end)
    assert window is not None
    assert window.due_from == NOW + timedelta(minutes=policy.preferred_minute)
    assert window.due_until == end
    late = trigger_window(end - timedelta(minutes=10), policy, end)
    assert late is not None and late.due_until - late.due_from == timedelta(minutes=10)
    all_day = policy_window(None, NOW, end)
    assert all_day is not None and all_day.kind == "all_day"
    assert trigger_window(end, policy, end) is None
    assert policy_window(None, end, end) is None


@pytest.mark.trace("T-E25-035")
def test_walk_and_repeat_windows_follow_source_instants() -> None:
    r = release()
    before = next(p for p in r.policies.values() if p.per_eva == "before_start")
    after = next(p for p in r.policies.values() if p.per_eva == "after_end")
    start = NOW + timedelta(hours=12)
    b = before_walk_window(start, start + timedelta(minutes=5), before, NOW)
    a = after_walk_window(start, after, NOW + timedelta(days=1))
    assert (
        b is not None
        and a is not None
        and before.window_minutes is not None
        and after.window_minutes is not None
    )
    assert b.due_from == start - timedelta(minutes=before.window_minutes)
    assert b.due_until == start + timedelta(minutes=5)
    assert a.due_until == start + timedelta(minutes=after.window_minutes)
    fallback = before_walk_window(start, None, None, NOW)
    assert fallback is not None and (fallback.due_from, fallback.due_until) == (NOW, start)
    slot = slot_window(start, 2, 30)
    assert slot is not None and (slot.due_from, slot.due_until) == (
        start + timedelta(hours=1),
        start + timedelta(minutes=90),
    )


@pytest.mark.trace("T-E25-021", "INV-E25-12")
def test_cadence_rendering_uses_configuration_and_missing_values_are_wholly_unknown() -> None:
    r = release()
    words = r.words
    assert words is not None
    for c in cadences():
        rendered = cadence_text(c, words, words.overlay_words["eva"])
        assert rendered and "{" not in rendered
        if c.note:
            assert rendered.endswith(", " + c.note)
    per_walk = next(c for c in cadences() if c.word == "per_eva")
    assert cadence_text(per_walk, words, None) == words.unknown
    assert render(words.end_text["suppressed"], {}, words.unknown) == words.unknown
    overlay = words.overlay_words["sleep"]
    assert render(words.end_text["suppressed"], {"overlay": overlay}, words.unknown) == words.end_text[
        "suppressed"
    ].replace("{overlay}", overlay)
    assert capitalize_first("") == ""
    assert hhmm("014/23:50:00") == "23:50"
    with pytest.raises(InvalidBody):
        hhmm("invalid")


@pytest.mark.parametrize(
    "state,today,specific,held,expected",
    [
        ("done", True, False, True, "done_today"),
        ("done", False, False, False, "done_earlier"),
        ("skipped", False, False, True, "skipped"),
        ("missed", False, False, True, "missed"),
        ("due", False, True, True, "suppressed"),
        ("later", False, False, False, "later"),
        ("due", False, True, False, "due_now"),
        ("due", False, False, False, "due_today"),
        ("not_due", False, False, False, "not_due"),
    ],
)
@pytest.mark.trace("T-E25-021")
def test_end_text_keys_follow_contract_first_match_order(
    state: OccurrenceState, today: bool, specific: bool, held: bool, expected: str
) -> None:
    # CONTRACTS 15 first-match table: final states precede hold, then later and due specificity.
    assert end_text_key(state, done_today=today, specific=specific, held=held) == expected


@pytest.mark.trace("T-E25-021")
def test_missing_period_is_configuration_error() -> None:
    c = next(c for c in cadences() if c.word == "every_n_days")
    with pytest.raises(ConfigInvalid):
        period_days(replace(c, n=None), release().calendar)

"""R61: composition and delivered-log invariants, including both sides of each bound."""

from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.catchup.application.ports import LogItem
from crew_health.catchup.domain.compose import compose_for_help
from crew_health.catchup.domain.errors import LogTooLarge
from crew_health.catchup.domain.log import select_items

from .test_compose import AT, Snapshot, Tick, ground_words, words


@pytest.mark.trace("R61")
@given(st.integers(0, 64), st.booleans(), st.booleans())
def test_help_sections_preserve_ticks_and_never_invent_watch_facts(
    count: int, called: bool, missing: bool
) -> None:
    snapshot = replace(
        Snapshot(),
        ticks=tuple(Tick(f"Step {i}.", None) for i in range(count)),
        crew_called_at=AT if called else None,
        place=None if missing else "airlock",
        answer_lines=() if missing else Snapshot().answer_lines,
    )
    result = compose_for_help(snapshot, words(), ground_words(), crew_time_text=None, plan=None)
    assert len(result.what_happened) == 2 and all(result.what_happened)
    assert len(result.what_was_done) == count + int(called)
    assert result.being_watched == ("Unknown",) and result.advice_welcome == ("Unknown",)
    assert words().nothing_watched not in result.being_watched
    assert result.what_was_done[:count] == tuple(f"Unknown Step {i}." for i in range(count))


@pytest.mark.trace("R61")
@given(
    st.lists(st.one_of(st.none(), st.integers(-10, 10)), max_size=50),
    st.integers(-10, 10),
    st.integers(1, 50),
)
def test_select_items_is_inclusive_ordered_complete_or_errors(
    offsets: list[int | None], start: int, limit: int
) -> None:
    rows = tuple(
        LogItem(
            "ground_message",
            f"gm_{i:03}",
            "Title",
            AT,
            None if offset is None else AT + timedelta(seconds=offset),
            (("Header",), (), ("Unknown",), ("Unknown",)),
            None,
            True,
        )
        for i, offset in enumerate(offsets)
    )
    since = AT + timedelta(seconds=start)
    expected = sorted(
        (r for r in rows if r.delivered_at is not None and r.delivered_at >= since),
        key=lambda r: (r.delivered_at, r.ref_id),
    )
    if len(expected) > limit:
        with pytest.raises(LogTooLarge):
            select_items(rows, since, limit)
    else:
        assert select_items(rows, since, limit) == tuple(expected)
        assert select_items(tuple(reversed(rows)), since, limit) == tuple(expected)
    all_delivered = tuple(
        sorted((r for r in rows if r.delivered_at is not None), key=lambda r: (r.delivered_at, r.ref_id))
    )
    assert select_items(rows, None, 50) == all_delivered


@pytest.mark.trace("R61")
def test_select_items_includes_exact_since_and_rejects_nonpositive_limit() -> None:
    row = LogItem("handoff", "handoff_1", "Title", AT, AT, (("Header",), (), (), ()), None, True)
    assert select_items((row,), AT, 1) == (row,)
    with pytest.raises(ValueError):
        select_items((row,), None, 0)

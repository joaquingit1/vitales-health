from datetime import timedelta
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.groundlink.domain.link import LinkStatus, ScheduleWindow, split_delay_text
from crew_health.groundlink.domain.queue import to_send

from .test_domain import AT, bundle, profile, state


@pytest.mark.trace("R48", "R49", "EI2")
@given(
    st.lists(
        st.tuples(st.integers(-1000, 1000), st.integers(1, 1000), st.sampled_from(["up", "delayed", "down"])),
        max_size=20,
    ),
    st.integers(-1500, 2500),
)
def test_random_windows_keep_gap_fields_consistent(values: list[tuple[int, int, str]], offset: int) -> None:
    windows = tuple(
        ScheduleWindow(
            AT + timedelta(seconds=start),
            AT + timedelta(seconds=start + duration),
            cast(LinkStatus, status),
            "equipment" if status == "down" else None,
            status == "down",
        )
        for start, duration, status in values
    )
    at = AT + timedelta(seconds=offset)
    link = state(profile(*windows), at=at)
    if link.status == "down":
        assert link.gap_kind is not None and link.next_contact_at is not None
        assert link.next_contact_at >= at
    else:
        assert link.gap_kind is None and not link.gap_planned and link.next_contact_at is None


@pytest.mark.trace("R48", "R49", "EI2")
@given(st.lists(st.integers(0, 1000), unique=True, max_size=40), st.integers(1, 20))
def test_queue_order_and_limit_have_both_upper_and_lower_bounds(indices: list[int], limit: int) -> None:
    items = tuple(bundle(i) for i in indices)
    selected = to_send(items, "delayed", AT + timedelta(days=1), limit)
    assert len(selected) == min(limit, len(items))
    assert selected == tuple(sorted(items, key=lambda b: (b.queued_at, b.bundle_id))[:limit])


@pytest.mark.trace("R48", "R49", "EI2")
@given(
    st.lists(
        st.tuples(st.sampled_from(["EV", "RT", "RS", "SK", "S", "C", "N", "L"]), st.integers(0, 99)),
        max_size=30,
    )
)
def test_source_groups_are_removed_and_ids_are_first_seen_input_subset(values: list[tuple[str, int]]) -> None:
    ids = [f"{prefix}{n:02}" for prefix, n in values]
    text = "Sentence (ordinary)." + "".join(" (" + i + ")." for i in ids)
    result, actual = split_delay_text(text, set(ids))
    assert "(ordinary)" in result
    assert actual == tuple(dict.fromkeys(ids))
    assert set(actual) <= set(ids)
    for i in ids:
        assert "(" + i + ")" not in result

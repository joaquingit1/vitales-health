from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.routine.domain.hold import Interval, held_kind_at, held_until, merged

from .support import NOW, release


def at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


@pytest.mark.trace("T-E25-091", "INV-E25-03")
def test_ended_sleep_before_task_does_not_extend_window() -> None:
    # E25 3.6.1: 13:00 to 15:00 task, sleep ending 08:00 leaves the window unchanged.
    assert held_until(at(780), at(900), (Interval(at(0), at(480), "sleep", "sleep"),)) == at(900)


@pytest.mark.trace("T-E25-091", "INV-E25-03")
def test_sleep_overlapping_walk_counts_once_and_order_does_not_matter() -> None:
    intervals = (Interval(at(780), at(840), "sleep", "s"), Interval(at(810), at(900), "eva", "e"))
    assert merged(intervals) == ((at(780), at(900)),)
    assert held_until(at(780), at(900), intervals) == at(1020)
    assert held_until(at(780), at(900), tuple(reversed(intervals))) == at(1020)
    assert held_until(at(780), at(900), (*intervals, Interval(at(950), None, "sleep", "open"))) is None
    assert held_until(at(780), at(900), (Interval(at(900), None, "sleep", "later"),)) == at(900)


@given(st.lists(st.tuples(st.integers(-20, 60), st.integers(0, 20)), max_size=12))
@pytest.mark.trace("T-E25-005", "INV-E25-03")
def test_hold_extension_equals_independent_discrete_union_measure_and_is_order_independent(
    raw: list[tuple[int, int]],
) -> None:
    intervals = tuple(
        Interval(at(start), at(start + length), "sleep", str(i)) for i, (start, length) in enumerate(raw)
    )
    result = held_until(at(0), at(20), intervals)
    # Independent clock simulation: consume twenty unheld minutes, counting each held minute once.
    held = {m for start, length in raw for m in range(max(0, start), start + length)}
    end, remaining = 0, 20
    for minute in range(20 + len(held)):
        if minute not in held:
            remaining -= 1
        end = minute + 1
        if remaining == 0:
            break
    assert result == at(end)
    assert result >= at(20)
    assert held_until(at(0), at(20), tuple(reversed(intervals))) == result


@pytest.mark.trace("T-E25-005")
def test_held_kind_uses_earliest_start_then_configured_order_and_half_open_bounds() -> None:
    order = release().suppressed_overlays
    intervals = tuple(Interval(at(0), at(10), kind, kind) for kind in order)
    assert held_kind_at(at(5), at(0), intervals, order) == order[0]
    assert held_kind_at(at(10), at(0), intervals, order) is None
    assert held_kind_at(at(-1), at(0), intervals, order) is None

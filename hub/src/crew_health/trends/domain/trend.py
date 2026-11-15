"""Daily latest values retain gaps and units without interpolation."""

from __future__ import annotations

from crew_health.trends.domain.baseline import order
from crew_health.trends.domain.errors import TrendsInvariant
from crew_health.trends.domain.model import DailyValue, Sample, TrendPoint

FIRST_MISSION_DAY = 1  # SYSTEM 5.3: mission day numbering starts at 1; earlier days are not mission days


def daily_points(samples: tuple[Sample, ...], *, current_day: int, trend_days: int) -> tuple[TrendPoint, ...]:
    """One point per mission day of the window; a day with no reading stays a missing point.

    The window is clamped to the first mission day, so early in a mission it is shorter instead of asking the clock
    for days that do not exist.
    """
    if trend_days < 1:
        raise TrendsInvariant("trend_days_invalid")
    by_day: dict[int, list[Sample]] = {}
    first = window_start(current_day, trend_days)
    for sample in sorted(samples, key=order):
        if sample.mission_day is not None and first <= sample.mission_day <= current_day:
            by_day.setdefault(sample.mission_day, []).append(sample)
    points: list[TrendPoint] = []
    for day in range(first, current_day + 1):
        values = by_day.get(day, [])
        latest = values[-1] if values else None
        points.append(
            TrendPoint(
                day,
                latest.quantity if latest else None,
                latest.observation_id if latest else None,
                len(values),
            )
        )
    return tuple(points)


def window_start(current_day: int, trend_days: int) -> int:
    return max(FIRST_MISSION_DAY, current_day - trend_days + 1)


def contiguous_tail(points: tuple[TrendPoint, ...]) -> tuple[DailyValue, ...]:
    tail: list[DailyValue] = []
    for point in reversed(points):
        if point.value is None:
            if tail:
                break
            continue
        if tail and point.mission_day != tail[-1].mission_day - 1:
            break
        tail.append(DailyValue(point.mission_day, point.value))
    if not tail or any(point.value.unit != tail[0].value.unit for point in tail):
        return ()
    return tuple(reversed(tail))

"""Daily observed values and gaps use injected mission days."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.testing import MemoryMissionClock
from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.domain.baseline import select
from crew_health.trends.domain.errors import TrendsInvariant
from crew_health.trends.domain.model import TrendPoint
from crew_health.trends.domain.trend import contiguous_tail, daily_points

from .test_baseline import AT, PARTITION, UNIT, sample


@pytest.mark.trace("E14-01", "V31", "AGENTS-1.5")
def test_daily_latest_is_deterministic_and_gaps_stay_missing() -> None:
    earlier = replace(sample(1, "7"), observation_id="obs_early", observed_at=AT - timedelta(days=1, hours=1))
    same_time = replace(sample(1, "8"), observation_id="obs_z")
    rows = (sample(0, "9"), sample(3, "2"), earlier, sample(1, "6"), same_time)
    result = daily_points(select(rows, PARTITION, AT).samples, current_day=20, trend_days=4)
    assert result == (
        TrendPoint(17, Quantity(Decimal("2"), UNIT), "obs_3", 1),
        TrendPoint(18, None, None, 0),
        TrendPoint(19, Quantity(Decimal("8"), UNIT), "obs_z", 3),
        TrendPoint(20, Quantity(Decimal("9"), UNIT), "obs_0", 1),
    )


@pytest.mark.trace("E14-01", "V31", "AGENTS-1.5")
def test_contiguous_tail_ends_at_last_non_null_and_stops_at_gap() -> None:
    points = (
        TrendPoint(16, Quantity(Decimal("1"), UNIT), "obs_a", 1),
        TrendPoint(17, None, None, 0),
        TrendPoint(18, Quantity(Decimal("2"), UNIT), "obs_b", 1),
        TrendPoint(19, Quantity(Decimal("3"), UNIT), "obs_c", 1),
        TrendPoint(20, None, None, 0),
    )
    result = contiguous_tail(points)
    assert [(item.mission_day, item.value.value) for item in result] == [
        (18, Decimal("2")),
        (19, Decimal("3")),
    ]
    assert contiguous_tail(()) == ()
    assert contiguous_tail((TrendPoint(1, None, None, 0),)) == ()


@pytest.mark.trace("E14-03", "AGENTS-1.8")
def test_mixed_units_and_nonconsecutive_days_cannot_form_one_tail() -> None:
    points = (
        TrendPoint(1, Quantity(Decimal("1"), UNIT), "obs_a", 1),
        TrendPoint(2, Quantity(Decimal("2"), Unit("s")), "obs_b", 1),
    )
    assert contiguous_tail(points) == ()
    separated = (points[0], replace(points[1], mission_day=3, value=Quantity(Decimal("2"), UNIT)))
    assert tuple(item.mission_day for item in contiguous_tail(separated)) == (3,)


@pytest.mark.trace("E14-01", "V14", "V31")
def test_mission_boundary_uses_memory_clock_versions() -> None:
    boundary = AT.replace(hour=0)
    clock = MemoryMissionClock(
        (
            MissionClockVersion(
                "v1", 1, boundary - timedelta(days=10), boundary - timedelta(days=10), boundary, "Day {day}"
            ),
            MissionClockVersion("v2", 2, boundary - timedelta(days=20), boundary, None, "Day {day}"),
        )
    )
    before = boundary - timedelta(microseconds=1)
    assert clock.mission_day(before) == 10
    assert clock.mission_day(boundary) == 21
    rows = (
        replace(sample(0), observed_at=before, mission_day=clock.mission_day(before)),
        replace(sample(1), observed_at=boundary, mission_day=clock.mission_day(boundary)),
    )
    points = daily_points(rows, current_day=clock.mission_day(AT), trend_days=2)
    assert points[0] == TrendPoint(20, None, None, 0)
    assert points[1].observation_id == "obs_1"


@pytest.mark.trace("E14-01", "AGENTS-3.5")
def test_domain_invariant_is_a_typed_problem_never_a_bare_exception() -> None:
    """m5 regression: an invalid catalog trend window surfaces as CONFIG_INVALID through the problem envelope."""
    with pytest.raises(ProblemError) as caught:
        daily_points((), current_day=3, trend_days=0)
    assert isinstance(caught.value, TrendsInvariant) and caught.value.code is ErrorCode.CONFIG_INVALID
    assert str(caught.value) == ErrorCode.CONFIG_INVALID.value and caught.value.reason == "trend_days_invalid"

"""E14-01: HTTP history, corrections and honest gaps on the real mission clock.

The E15 EpisodeReader fake supplies a complete empty set of episode periods.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from crew_health.trends.application.ports import BaselineSummary, InsufficientBaseline, Unavailable

from .support import NOW, ScenarioHub, history

pytestmark = pytest.mark.trace("E14-01", "R11", "R12", "V07", "V14", "V31")


def test_usual_excludes_current_context_and_superseded_readings(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path)
    ids = history(hub)
    exercise = hub.capture(NOW - timedelta(days=3), 900, context={"activity": "exercise"})
    corrected = hub.capture(NOW - timedelta(days=4), 35, supersedes=ids[2])
    summary = hub.read()
    usual = summary.usual
    assert isinstance(usual, BaselineSummary)
    assert usual.value.value == 35
    assert usual.value.unit.code == "/min"
    assert usual.sample_count == 5
    assert set(usual.included_ids) == {ids[0], ids[1], corrected, ids[3], ids[4]}
    excluded = {(item.observation_id, item.reason) for item in usual.exclusions}
    assert {(ids[-1], "current_reading"), (exercise, "cohort_mismatch"), (ids[2], "superseded")} <= excluded
    assert summary.today is not None
    assert summary.today.observation_id == ids[-1]
    assert usual.comparison.basis == "team default"
    assert usual.comparison.source_ids == ()
    assert usual.comparison.to_be_set_by == "clinical review"
    assert isinstance(summary.trend, tuple)
    assert tuple(point.mission_day for point in summary.trend) == (8, 9, 10, 11, 12, 13, 14)
    gaps = tuple(point for point in summary.trend if point.value is None)
    assert tuple(point.mission_day for point in gaps) == (9,)
    assert all(point.observation_id is None and point.readings_that_day == 0 for point in gaps)
    day_ten = summary.trend[2]
    assert day_ten.observation_id == corrected
    assert day_ten.value == usual.value


def test_missing_days_and_insufficient_history_stay_explicit(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path)
    previous = hub.post(12, 40)
    current = hub.post(14, 45)
    summary = hub.read()
    assert isinstance(summary.usual, InsufficientBaseline)
    assert summary.usual.sample_count == 1 < summary.usual.min_samples
    assert summary.usual.included_ids == (previous,)
    assert summary.today is not None
    assert summary.today.observation_id == current
    assert summary.state == "no_baseline"
    assert summary.delta is None
    assert isinstance(summary.trend, tuple)
    assert tuple(point.mission_day for point in summary.trend if point.value is None) == (8, 9, 10, 11, 13)
    assert all(point.value is None for point in summary.trend if point.observation_id is None)


def test_absent_episode_provider_preserves_readings_and_trend(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path, episodes=False)
    ids = history(hub)
    summary = hub.read()
    assert summary.usual == Unavailable("E15", "episode_periods_unavailable")
    assert summary.today is not None
    assert summary.today.observation_id == ids[-1]
    assert summary.latest == summary.today
    assert isinstance(summary.trend, tuple)
    assert summary.trend[-1].value == summary.today.value
    assert tuple(point.mission_day for point in summary.trend if point.value is None) == (9, 11)


def test_fresh_mission_trend_starts_at_day_one(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path, epoch=NOW - timedelta(days=2, hours=12))
    current = hub.capture(NOW - timedelta(hours=1), 45)
    summary = hub.read()
    assert hub.clock.mission_day(NOW) == 3
    assert summary.today is not None
    assert summary.today.observation_id == current
    assert summary.today.mission_day == 3
    assert isinstance(summary.trend, tuple)
    assert tuple(point.mission_day for point in summary.trend) == (1, 2, 3)
    assert tuple(point.value for point in summary.trend) == (None, None, summary.today.value)

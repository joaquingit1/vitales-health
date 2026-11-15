from dataclasses import dataclass
from datetime import datetime, timedelta

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.release import ItemPolicy
from crew_health.routine.domain.vocabulary import DueKind


@dataclass(frozen=True, slots=True)
class Window:
    due_from: datetime
    due_until: datetime
    kind: DueKind

    def __post_init__(self) -> None:
        require_utc(self.due_from)
        require_utc(self.due_until)


def _window(start: datetime, end: datetime, kind: DueKind = "specific") -> Window | None:
    return Window(start, end, kind) if end > start else None


def policy_window(policy: ItemPolicy | None, day_start: datetime, day_end: datetime) -> Window | None:
    if policy is None or policy.preferred_minute is None:
        return _window(day_start, day_end, "all_day")
    start = day_start + timedelta(minutes=policy.preferred_minute)
    return _window(
        start, day_end if policy.window_minutes is None else start + timedelta(minutes=policy.window_minutes)
    )


def trigger_window(t: datetime, policy: ItemPolicy | None, day_end_of_t: datetime) -> Window | None:
    end = (
        day_end_of_t
        if policy is None or policy.window_minutes is None
        else t + timedelta(minutes=policy.window_minutes)
    )
    return _window(t, end)


def slot_window(t: datetime, k: int, every_minutes: int) -> Window | None:
    return _window(t + timedelta(minutes=k * every_minutes), t + timedelta(minutes=(k + 1) * every_minutes))


def after_walk_window(
    ended_at: datetime, policy: ItemPolicy | None, day_end_of_end: datetime
) -> Window | None:
    return trigger_window(ended_at, policy, day_end_of_end)


def before_walk_window(
    planned_start: datetime,
    started_at: datetime | None,
    policy: ItemPolicy | None,
    day_start_of_walk_day: datetime,
) -> Window | None:
    start = (
        day_start_of_walk_day
        if policy is None or policy.window_minutes is None
        else planned_start - timedelta(minutes=policy.window_minutes)
    )
    return _window(start, planned_start if started_at is None else started_at)

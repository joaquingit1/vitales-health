from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from crew_health.routine.domain.release import MissesPolicy


@dataclass(frozen=True, slots=True)
class MissFact:
    item_id: str
    mission_day: int
    counted: bool


@dataclass(frozen=True, slots=True)
class MissesSummary:
    count: int
    window_days: int
    barrier_question_due: bool
    item_ids: tuple[str, ...]


def window_for(policy: MissesPolicy, period: int | None) -> int:
    return (
        policy.window_days
        if period is None
        else max(policy.window_days, (policy.ask_barrier_after_misses - 1) * period)
    )


def summarize(
    facts: Sequence[MissFact],
    policy: MissesPolicy,
    periods: Mapping[str, int | None],
    last_ask_day: int | None,
) -> MissesSummary:
    days: dict[str, list[int]] = {}
    for fact in facts:
        if fact.counted and (last_ask_day is None or fact.mission_day > last_ask_day):
            days.setdefault(fact.item_id, []).append(fact.mission_day)
    qualifying: list[str] = []
    count = 0
    needed = policy.ask_barrier_after_misses
    for item_id, item_days in sorted(days.items()):
        ordered = sorted(item_days)
        window = window_for(policy, periods.get(item_id))
        if any(
            ordered[i + needed - 1] - ordered[i] <= window for i in range(max(0, len(ordered) - needed + 1))
        ):
            qualifying.append(item_id)
            count += len(ordered)
    return MissesSummary(count, policy.window_days, bool(qualifying), tuple(qualifying))

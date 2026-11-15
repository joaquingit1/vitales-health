from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.errors import InvalidBody
from crew_health.routine.domain.vocabulary import Overlay


@dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime | None
    kind: Overlay
    overlay_id: str

    def __post_init__(self) -> None:
        require_utc(self.start)
        if self.end is not None:
            require_utc(self.end)
            if self.end < self.start:
                raise InvalidBody("/end", "before_start")


def merged(intervals: Sequence[Interval]) -> tuple[tuple[datetime, datetime | None], ...]:
    result: list[tuple[datetime, datetime | None]] = []
    for interval in sorted(intervals, key=lambda i: i.start):
        if not result:
            result.append((interval.start, interval.end))
            continue
        start, end = result[-1]
        if end is None:
            break
        if interval.start <= end:
            result[-1] = (start, None if interval.end is None else max(end, interval.end))
        else:
            result.append((interval.start, interval.end))
    return tuple(result)


def held_until(f: datetime, u: datetime, intervals: Sequence[Interval]) -> datetime | None:
    end = u
    for a, b in merged(intervals):
        if a >= end:
            break
        start = max(a, f)
        if b is None:
            return None
        if b > start:
            end += b - start
    return end


def held_kind_at(
    t: datetime, f: datetime, intervals: Sequence[Interval], kind_order: tuple[Overlay, ...]
) -> Overlay | None:
    if t < f:
        return None
    containing = [i for i in intervals if i.start <= t and (i.end is None or t < i.end)]
    if not containing:
        return None
    return min(
        containing,
        key=lambda i: (
            i.start,
            kind_order.index(i.kind) if i.kind in kind_order else len(kind_order),
            i.kind,
        ),
    ).kind

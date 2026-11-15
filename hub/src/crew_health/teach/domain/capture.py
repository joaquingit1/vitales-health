"""Count capture (E30 3.3): the completeness rule CR1 to CR4, a safety invariant in code. Its one threshold,
the maximum gap between beats, is injected (hub setting interim, X5). Durations use hub elapsed time only (3.8)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from crew_health.teach.domain.errors import InvalidValue

NS_PER_SECOND = 1_000_000_000

# Reasons an attempt is incomplete; each is a wire-safe code, never a value.
NO_BEATS = "no_beats"
SEQUENCE_GAP = "sequence_gap"
LATE_START = "late_start"
LOST_END = "lost_end"
INNER_GAP = "inner_gap"
SEQUENCE_CONFLICT = "sequence_conflict"
HUB_RESTART = "hub_restart"
CLOCK_DISCONTINUITY = "clock_discontinuity"


@dataclass(frozen=True, slots=True)
class ClockReading:
    """One hub clock reading: UTC, process monotonic elapsed time and the process generation (3.8)."""

    utc: datetime
    elapsed_ns: int
    generation: str


@dataclass(frozen=True, slots=True)
class Beat:
    sequence: int
    elapsed_ns: int  # since the window start, same generation
    at_utc: datetime  # window start plus elapsed: hub derived, never the client's clock (I30-05)
    body_sha256: str


@dataclass(frozen=True, slots=True)
class CountWindow:
    attempt_no: int
    started_utc: datetime
    started_elapsed_ns: int
    generation: str
    count_seconds: int
    opened_client_time: datetime  # attempt binding only (3.3 step 2), never a duration or a stored instant

    def elapsed_ns(self, reading: ClockReading) -> int:
        return reading.elapsed_ns - self.started_elapsed_ns

    def ends_utc(self) -> datetime:
        return self.started_utc + timedelta(seconds=self.count_seconds)

    def window_ns(self) -> int:
        return self.count_seconds * NS_PER_SECOND

    def ended(self, reading: ClockReading) -> bool:
        if reading.generation != self.generation:
            return reading.utc >= self.ends_utc()
        return self.elapsed_ns(reading) >= self.window_ns()

    def seconds_left(self, reading: ClockReading) -> int:
        left = self.window_ns() - self.elapsed_ns(reading)
        return max(0, -(-left // NS_PER_SECOND))

    def diverged(self, reading: ClockReading, tolerance_ms: int) -> bool:
        """True when the raw UTC reading drifted from the anchored instant: a clock step or a suspension (3.8)."""
        derived = self.started_utc + timedelta(microseconds=self.elapsed_ns(reading) // 1000)
        return abs(reading.utc - derived) > timedelta(milliseconds=tolerance_ms)


def completeness(
    beats: Mapping[int, Beat], window: CountWindow, flags: frozenset[str], *, max_gap_seconds: int
) -> frozenset[str]:
    """Reasons the capture is incomplete; empty means complete (CR1 to CR4)."""
    reasons: set[str] = set(flags & {SEQUENCE_CONFLICT, HUB_RESTART, CLOCK_DISCONTINUITY})
    if not beats:
        return frozenset(reasons | {NO_BEATS})
    ordered = [beats[k] for k in sorted(beats)]
    if [b.sequence for b in ordered] != list(range(1, len(ordered) + 1)):
        reasons.add(SEQUENCE_GAP)
    gap = max_gap_seconds * NS_PER_SECOND
    times = sorted(b.elapsed_ns for b in ordered)
    if times[0] > gap:
        reasons.add(LATE_START)
    if times[-1] < window.window_ns() - gap:
        reasons.add(LOST_END)
    if any(later - earlier > gap for earlier, later in zip(times, times[1:], strict=False)):
        reasons.add(INNER_GAP)
    return frozenset(reasons)


def count_per_minute(beat_count: int, count_seconds: int) -> Fraction:
    """Exact rate: beats times 60 over the window seconds (CONTRACTS 23 step 1)."""
    if beat_count < 0 or count_seconds < 1:
        raise InvalidValue("count_out_of_range")
    return Fraction(beat_count * 60, count_seconds)

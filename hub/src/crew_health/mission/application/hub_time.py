"""Normalization of a hub stamped instant through E01's clock ports (S5.2, E23 3.1).

The hub's own capture stream is named by the `mission.hub_source_clock_id` setting. With no segment for it the
instant is unknown and no state is ordered by it: fail closed, never receipt time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import ClockCorrections, ClockSegments, NormalizedTime, TimeInput
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.mission.application.use_cases import EventTimeFacts
from crew_health.mission.domain.events import TimeState

_NEWEST = 2**53 - 1  # I-JSON exact range: the newest segment of the stream covers this sequence


def time_input(
    segments: ClockSegments,
    view: ReadSnapshot | UnitOfWork,
    *,
    source_clock_id: str,
    at: datetime,
    origin_mode: Literal["LIVE", "REPLAY", "SIM"],
) -> TimeInput:
    segment = segments.for_sequence(view, source_clock_id=source_clock_id, sequence=_NEWEST)
    if segment is None:
        return TimeInput(at, source_clock_id, "unmapped", 0, "hub_corrected", origin_mode)
    return TimeInput(
        at, source_clock_id, segment.segment_id, segment.capture_offset_version, "hub_corrected", origin_mode
    )


def event_time(
    corrections: ClockCorrections | None,
    segments: ClockSegments | None,
    view: ReadSnapshot | UnitOfWork,
    *,
    source_clock_id: str,
    at: datetime,
    origin_mode: Literal["LIVE", "REPLAY", "SIM"],
) -> EventTimeFacts:
    if corrections is None or segments is None:
        return EventTimeFacts("unknown", None)
    stamp = time_input(segments, view, source_clock_id=source_clock_id, at=at, origin_mode=origin_mode)
    normalized: NormalizedTime = corrections.normalize(view, stamp)
    state: TimeState = normalized.state
    return EventTimeFacts(state, normalized.corrected_at if state in ("known", "replay") else None)

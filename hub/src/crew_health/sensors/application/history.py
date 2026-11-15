"""The bounded recent history a plausibility assessment reads (R-E23-2, DE23-11)."""

from __future__ import annotations

from datetime import datetime

from crew_health.sensors.application.runtime import Runtime
from crew_health.sensors.application.store import ReadingRecord, View
from crew_health.sensors.domain.plausibility import STATIC_FAILURES, SUSPECT_REASONS
from crew_health.sensors.domain.reading import ReadingFact


def fact_of(record: ReadingRecord) -> ReadingFact:
    return ReadingFact(
        record.reading_id,
        record.channel_id,
        record.adapter_sequence,
        record.corrected_at,
        record.received_at,
        record.time_state,
        record.canonical,
        record.quality_status,
        record.origin_mode,
        record.segment_id,
        record.provenance_trace if record.provenance_trace in SUSPECT_REASONS else None,
    )


def plausibility_history(
    rt: Runtime, view: View, channel_id: str, before: datetime
) -> tuple[ReadingFact, ...]:
    """Earlier readings of the channel, oldest first, at most `sensors.plausibility_history_max`.

    A reading that failed range or saturation has no usable value and is left out; one that failed as stuck or too
    fast stays (stored as suspect), because its value is a real measurement the next reading is compared with.
    """
    records = rt.store.recent_readings(
        view, channel_id, before=before, limit=rt.limits.plausibility_history_max
    )
    usable = [r for r in records if r.provenance_trace not in STATIC_FAILURES]
    return tuple(fact_of(r) for r in reversed(usable))

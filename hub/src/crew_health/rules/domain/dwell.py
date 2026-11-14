"""Dwell continuity of a sensor-limit clear (E24 3.6).

The clear condition must hold continuously for `dwell_seconds` of observation time. Continuity is
proved, never assumed: the same evaluation instant never advances a dwell, a gap above the channel's
stale bound, a different clock segment, a missing or false result, or any ineligible evaluation breaks it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class DwellMarker:
    warning_id: str
    channel_id: str
    rule_fingerprint: str
    holding_since: datetime
    last_window_end: datetime
    segment_key: str


@dataclass(frozen=True, slots=True)
class DwellOutcome:
    action: Literal["none", "put", "delete"]  # internal enum
    marker: DwellMarker | None
    clear: bool


_NOTHING = DwellOutcome("none", None, False)


def step(
    marker: DwellMarker | None,
    *,
    warning_id: str,
    channel_id: str,
    e: datetime | None,
    segment_key: str,
    eligible: bool,
    dwell_seconds: int,
    gap_bound_seconds: int,
    fingerprint: str,
) -> DwellOutcome:
    """One evaluation of a warning's dwell. `e` is the evaluation instant on the data's own time axis."""
    if marker is not None and marker.rule_fingerprint != fingerprint:
        marker = None  # a marker of another rule is void; the delete below is not needed, put replaces it
        stale_marker = True
    else:
        stale_marker = False
    if not eligible or e is None:
        return DwellOutcome("delete", None, False) if (marker is not None or stale_marker) else _NOTHING
    if marker is not None and marker.segment_key != segment_key:
        marker = None
    if marker is not None:
        if e <= marker.last_window_end:
            return _NOTHING  # same or older evaluation instant: eligible, but it advances nothing
        if (e - marker.last_window_end).total_seconds() > gap_bound_seconds:
            marker = None
    if marker is None:
        fresh = DwellMarker(warning_id, channel_id, fingerprint, e, e, segment_key)
        return DwellOutcome("put", fresh, dwell_seconds == 0)
    advanced = replace(marker, last_window_end=e)
    return DwellOutcome("put", advanced, (e - advanced.holding_since).total_seconds() >= dwell_seconds)

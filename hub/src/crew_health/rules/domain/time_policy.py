"""Time rules of the engine (E24 3.11), as pure predicates over injected instants.

No wall clock is read here. Relative timers (assistant expiry, the Q9 wake) are expressed as elapsed
seconds and run on E01's elapsed anchors, so a clock correction moves them with the clock. Absolute
deadlines (the ground contact time) are never shifted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

_DAY_SECONDS = 86400


def ground_silence_due_at(next_contact_at: datetime, threshold_seconds: int) -> datetime:
    """Absolute instant at which a down link past its contact time raises the warning."""
    return next_contact_at + timedelta(seconds=threshold_seconds)


def ground_silence_holds(
    *,
    now: datetime,
    status: str,
    next_contact_at: datetime | None,
    threshold_seconds: int | None,
    gap_planned: bool,
) -> bool:
    """The raise condition. A null threshold raises nothing, and a planned gap raises nothing (C26)."""
    if threshold_seconds is None or next_contact_at is None or gap_planned or status != "down":
        return False
    return now >= ground_silence_due_at(next_contact_at, threshold_seconds)


def ground_silence_ended(*, status: str, next_contact_at: datetime | None, raised_for: datetime) -> bool:
    """The clear condition. Only the link state ends it, never the clock: a backward correction never clears."""
    if status in {"up", "delayed"}:
        return True
    return next_contact_at is not None and next_contact_at > raised_for


def expiry_due(*, elapsed_seconds: int, expires_after_seconds: int | None, clock_regressed: bool) -> bool:
    """Assistant expiry on elapsed time, suspended while the clock is regressed (E24 3.11, S5.2)."""
    if expires_after_seconds is None or clock_regressed:
        return False
    return elapsed_seconds >= expires_after_seconds


def answer_window_satisfied(
    *,
    answered_at: datetime,
    answered_corrected_at: datetime | None,
    occurred_at: datetime,
    occurred_corrected_at: datetime | None,
    days: int,
) -> bool:
    """the difference from the event to the answer lies between zero and `days` x 86400 seconds, on the recorded and the corrected difference.

    It holds when either difference satisfies it, so a correction never hides a warning.
    """
    limit = days * _DAY_SECONDS
    recorded = (answered_at - occurred_at).total_seconds()
    if 0 <= recorded <= limit:
        return True
    if answered_corrected_at is None or occurred_corrected_at is None:
        return False
    corrected = (answered_corrected_at - occurred_corrected_at).total_seconds()
    return 0 <= corrected <= limit

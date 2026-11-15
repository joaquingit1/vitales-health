"""Channel freshness from corrected time (decisions D3, D14; E23 3.1, 3.4).

Freshness never comes from arrival time. A value is current only when its whole uncertainty interval lies in
(now - stale_after, now]; stale is strictly greater than the duration. Unknown, future or regressed time is
never current. REPLAY uses the receive reference of the reading that last advanced E (D14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.sensors.domain.channel import Freshness, Origin

ClockState = Literal["trusted", "uncertain", "unknown"]  # E24 vocabulary (E23 3.1)
NormalizedState = Literal["known", "unknown", "future", "replay"]  # S5.2 NormalizedTime.state


@dataclass(frozen=True, slots=True)
class TimeFacts:
    state: NormalizedState
    corrected_at: datetime | None
    uncertainty_ms: int | None


@dataclass(frozen=True, slots=True)
class FreshnessDecision:
    state: Freshness
    clock_state: ClockState
    stale_since: datetime | None
    next_check_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class ReplayReference:
    """D14: E and the receive time of the reading that strictly advanced it."""

    window_end: datetime
    receive: TimeFacts


def advance_replay_reference(
    current: ReplayReference | None, observed_at: datetime, receive: TimeFacts
) -> ReplayReference:
    """Only a strictly advancing observation replaces the reference; restarts, repeats and loops never do."""
    if current is None or observed_at > current.window_end:
        return ReplayReference(observed_at, receive)
    return current


def select_reference(
    origin: Origin, observed: TimeFacts | None, replay: ReplayReference | None
) -> TimeFacts | None:
    """LIVE and SIM use the normalized observation (D3); REPLAY the D14 receive reference."""
    if origin == "REPLAY":
        return None if replay is None else replay.receive
    return observed


def _decision(
    state: Freshness,
    clock: ClockState,
    reason: str,
    stale_since: datetime | None = None,
    next_check_at: datetime | None = None,
) -> FreshnessDecision:
    return FreshnessDecision(state, clock, stale_since, next_check_at, reason)


def _untrusted(reference: TimeFacts) -> FreshnessDecision | None:
    if reference.state == "future":
        return _decision("stale", "uncertain", "future_time")
    if reference.state == "unknown" or reference.corrected_at is None or reference.uncertainty_ms is None:
        return _decision("stale", "unknown", "unknown_time")
    return None


def decide_freshness(
    *,
    now: datetime,
    stale_after_seconds: int,
    has_history: bool,
    reference: TimeFacts | None,
    quiet_anchor: TimeFacts | None = None,
    regressed: bool = False,
) -> FreshnessDecision:
    """Decide current, stale or never_reported. Unproven time fails closed as stale."""
    if not has_history:
        return _decision("never_reported", "unknown", "no_history")
    chosen = reference if reference is not None else quiet_anchor
    if chosen is None:
        return _decision("stale", "unknown", "no_time_reference")
    if regressed:
        return _decision("stale", "unknown", "clock_regressed")
    refusal = _untrusted(chosen)
    if refusal is not None:
        return refusal
    corrected = chosen.corrected_at
    uncertainty = chosen.uncertainty_ms
    if corrected is None or uncertainty is None:  # narrowed for the type checker; _untrusted covers it
        return _decision("stale", "unknown", "unknown_time")
    spread = timedelta(milliseconds=uncertainty)
    lower, upper = corrected - spread, corrected + spread
    deadline = lower + timedelta(seconds=stale_after_seconds)
    if upper > now:
        return _decision("stale", "uncertain", "interval_not_yet_past", None, upper)
    if now > deadline:
        return _decision("stale", "trusted", "past_deadline", deadline)
    return _decision("current", "trusted", "within_window", None, deadline)

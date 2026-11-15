"""Which sensor evaluations may count toward a clear (E24 3.6, D3, D14).

Only evidence observed strictly after the warning was raised, in a current, trusted window of the same
origin, that was evaluated under the warning's own pinned limit and whose clear condition holds, counts.
A delayed, duplicated or replayed input never clears; unknown, future or uncertain time never clears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.rules.domain.inputs import LimitResult, SensorEvaluatedInput
from crew_health.rules.domain.origin import clears_across_origin
from crew_health.rules.domain.vocabulary import OriginMode
from crew_health.rules.domain.warning import Warning

type Reason = Literal[
    "eligible",
    "origin_mismatch",
    "clock_not_trusted",
    "freshness_not_current",
    "time_unknown",
    "not_fresh",
    "no_receive_reference",
    "not_after_raise",
    "pin_unavailable",
    "limit_missing",
    "clear_not_holding",
]  # internal enum


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    reason: Reason


def _no(reason: Reason) -> Eligibility:
    return Eligibility(False, reason)


def pinned_result(inp: SensorEvaluatedInput, w: Warning) -> LimitResult | None:
    """The result of the warning's own limit, accepted only when evaluated under its pin (R-E23-19).

    A result carrying the pinned limit fingerprint is accepted. A result carrying none (the interim,
    before R-E23-19) is accepted only when the input was computed under the warning's own release, where
    the limit is the pinned one by construction. Any other result is not evaluable under the pin.
    """
    pin = w.pin.limit
    if pin is None:
        return None
    result = inp.result_for(pin.limit_id)
    if result is None:
        return None
    if result.limit_fingerprint is not None:
        return result if result.limit_fingerprint == pin.limit_fingerprint else None
    return result if inp.config_release_id == w.config_release_id else None


def _live_time(inp: SensorEvaluatedInput, w: Warning, now: datetime, bound: int) -> Reason | None:
    interval = inp.observed.interval()
    if interval is None:
        return "time_unknown"
    low, high = interval
    if high > now:
        return "time_unknown"  # a reading from the future is never evidence
    if low < now - timedelta(seconds=bound):
        return "not_fresh"
    if low <= w.raised_at:
        return "not_after_raise"
    return None


def _replay_time(inp: SensorEvaluatedInput, w: Warning, now: datetime, bound: int) -> Reason | None:
    axis = inp.observed.axis()
    if axis is None:
        return "time_unknown"
    if inp.receive_reference is None:
        return "no_receive_reference"
    age = (now - inp.receive_reference).total_seconds()
    if age < 0 or age > bound:
        return "not_fresh"
    if axis <= w.evidence_time:
        return "not_after_raise"
    return None


def eligible(inp: SensorEvaluatedInput, w: Warning, now: datetime) -> Eligibility:
    """Whether this evaluation may count toward `w`'s dwell. Pure; `now` is injected."""
    pin = w.pin.limit
    if pin is None:
        return _no("pin_unavailable")
    if not clears_across_origin(evidence_origin=inp.origin_mode, warning_origin=w.origin_mode):
        return _no("origin_mismatch")
    if inp.clock_state != "trusted":
        return _no("clock_not_trusted")
    if inp.freshness != "current":
        return _no("freshness_not_current")
    bound = min(inp.stale_after_seconds, pin.stale_after_seconds)
    if inp.origin_mode is OriginMode.REPLAY:
        failure = _replay_time(inp, w, now, bound)
    else:
        failure = _live_time(inp, w, now, bound)
    if failure is not None:
        return _no(failure)
    result = pinned_result(inp, w)
    if result is None:
        return _no("limit_missing" if inp.result_for(pin.limit_id) is None else "pin_unavailable")
    if result.clear_holds is not True or result.crossed is True:
        return _no("clear_not_holding")
    return Eligibility(True, "eligible")

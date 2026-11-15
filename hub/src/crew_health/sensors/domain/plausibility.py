"""Plausibility gate (DE23-11, R-E23-2): range, saturation, stuck value and rate of change on every input.

The four criteria come from the sensor catalog, each with its sources; nothing here holds a number. A sensor type
without criteria cannot be assessed: the answer is "unavailable" and the reading is captured but never evaluated.
A check that cannot be proved from the history at hand is also "unavailable": missing history never passes a
stuck or rate check. All four checks run in every origin mode, so LIVE, REPLAY and SIM take the same path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from crew_health.sensors.domain.channel import (
    Origin,
    PlausibilityCriteria,
    RegistrationPolicy,
    SensorTypeFacts,
)
from crew_health.sensors.domain.reading import MICROSECOND, ReadingFact, micros

PlausibilityStatus = Literal["unavailable", "passed", "failed"]

CRITERIA_MISSING = "criteria_missing"
TIME_UNAVAILABLE = "time_unavailable"
INSUFFICIENT_HISTORY = "insufficient_history"
HISTORY_BOUND_TOO_SMALL = "history_bound_below_stuck_count"
OUT_OF_RANGE = "out_of_range"
SATURATED = "saturated"
STUCK_VALUE = "stuck_value"
RATE_EXCEEDED = "rate_exceeded"
# The check names stored in the provenance trace of a failed reading.
RANGE_CHECK = "range"
SATURATION_CHECK = "saturation"
STUCK_CHECK = "stuck"
RATE_CHECK = "rate"
STATIC_FAILURES = frozenset({OUT_OF_RANGE, SATURATED})


@dataclass(frozen=True, slots=True)
class PlausibilityResult:
    status: PlausibilityStatus
    reason: str
    failed_check: str | None

    @property
    def evaluable(self) -> bool:
        return self.status == "passed"


def _static_failure(criteria: PlausibilityCriteria, value: Decimal) -> PlausibilityResult | None:
    if value < criteria.minimum or value > criteria.maximum:
        return PlausibilityResult("failed", OUT_OF_RANGE, RANGE_CHECK)
    if value in criteria.saturation:
        return PlausibilityResult("failed", SATURATED, SATURATION_CHECK)
    return None


def _seconds(later: ReadingFact, earlier: ReadingFact) -> Decimal | None:
    if later.at is None or earlier.at is None:
        return None
    return Decimal((later.at - earlier.at) // MICROSECOND) / Decimal(1_000_000)


def _rate(
    criteria: PlausibilityCriteria, reading: ReadingFact, history: Sequence[ReadingFact]
) -> PlausibilityResult | None:
    """None means the check passed. Without an earlier reading it cannot be proved."""
    if not history:
        return PlausibilityResult("unavailable", INSUFFICIENT_HISTORY, None)
    elapsed = _seconds(reading, history[-1])
    if elapsed is None or elapsed <= 0:
        return PlausibilityResult("unavailable", TIME_UNAVAILABLE, None)
    if abs(reading.value - history[-1].value) > criteria.max_rate_per_s * elapsed:
        return PlausibilityResult("failed", RATE_EXCEEDED, RATE_CHECK)
    return None


def _run_start(reading: ReadingFact, history: Sequence[ReadingFact]) -> tuple[int, ReadingFact]:
    """How many readings, the current one included, hold the current value without a break, and the oldest."""
    length, oldest = 1, reading
    for earlier in reversed(history):
        if earlier.value != reading.value:
            break
        length, oldest = length + 1, earlier
    return length, oldest


def _stuck(
    criteria: PlausibilityCriteria, reading: ReadingFact, history: Sequence[ReadingFact], history_bound: int
) -> PlausibilityResult | None:
    """Stuck is a run of at least `stuck_count` identical values that also spans `stuck_min_span_s`.

    A run that fills the whole history cannot be measured: with fewer than `stuck_count` readings it is too short to
    judge, and with a full history it may be older than anything visible. Neither proves the sensor is not stuck.
    """
    if criteria.stuck_count - 1 > history_bound:
        return PlausibilityResult("unavailable", HISTORY_BOUND_TOO_SMALL, None)
    length, oldest = _run_start(reading, history)
    fills_history = length == len(history) + 1
    if length < criteria.stuck_count:
        if fills_history:
            return PlausibilityResult("unavailable", INSUFFICIENT_HISTORY, None)
        return None  # a different value inside the last stuck_count readings proves it moves
    span = _seconds(reading, oldest)
    if span is None:
        return PlausibilityResult("unavailable", TIME_UNAVAILABLE, None)
    if span >= criteria.stuck_min_span_s:
        return PlausibilityResult("failed", STUCK_VALUE, STUCK_CHECK)
    if fills_history and len(history) >= history_bound:
        return PlausibilityResult("unavailable", INSUFFICIENT_HISTORY, None)
    return None


def assess(
    sensor_type: SensorTypeFacts,
    reading: ReadingFact,
    history: Sequence[ReadingFact] = (),
    *,
    history_bound: int = 0,
) -> PlausibilityResult:
    """All four checks. `history` holds earlier readings of the same channel and origin, oldest first, at most
    `history_bound` of them; readings that failed range or saturation are not in it (they carry no usable value).

    A failed check wins over an unproved one; a pass needs every check proved.
    """
    criteria = sensor_type.plausibility
    if criteria is None:
        return PlausibilityResult("unavailable", CRITERIA_MISSING, None)
    failure = _static_failure(criteria, reading.value)
    if failure is not None:
        return failure
    if reading.at is None or reading.time_state not in ("known", "replay"):
        return PlausibilityResult("unavailable", TIME_UNAVAILABLE, None)
    earlier = sorted(
        (item for item in history if item.at is not None and item.at < reading.at),
        key=lambda item: micros(item.at) if item.at is not None else 0,
    )
    earlier = earlier[max(0, len(earlier) - history_bound) :]
    outcomes = (
        _rate(criteria, reading, earlier),
        _stuck(criteria, reading, earlier, history_bound),
    )
    for outcome in outcomes:
        if outcome is not None and outcome.status == "failed":
            return outcome
    for outcome in outcomes:
        if outcome is not None:
            return outcome
    return PlausibilityResult("passed", "plausible", None)


def registration_policy(origin: Origin, *, criteria_available: bool) -> RegistrationPolicy:
    """T-E23-070: LIVE is refused without criteria; REPLAY and SIM are captured but never evaluated."""
    if criteria_available:
        return RegistrationPolicy(True, True, True, "criteria_available")
    if origin == "LIVE":
        return RegistrationPolicy(False, False, False, CRITERIA_MISSING)
    return RegistrationPolicy(True, True, False, CRITERIA_MISSING)

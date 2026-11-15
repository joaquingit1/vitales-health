from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.clock.domain.errors import InvalidClockValue
from crew_health.kernel.clock import ClockCorrection, NormalizedTime, TimeInput, require_utc


def validate_correction(correction: ClockCorrection, *, maximum_uncertainty_ms: int) -> None:
    if (
        not correction.correction_id
        or not correction.source_clock_id
        or not correction.segment_id
        or type(correction.version) is not int
        or correction.version < 1
        or type(correction.offset_ms) is not int
        or not -9223372036854775808 <= correction.offset_ms <= 9223372036854775807
        or type(correction.uncertainty_ms) is not int
        or not 0 <= correction.uncertainty_ms <= maximum_uncertainty_ms
        or correction.source not in ("operator", "measured", "ground")
        or (correction.valid_until is not None and correction.valid_from >= correction.valid_until)
    ):
        raise InvalidClockValue()


def raw_time(value: TimeInput, captured: ClockCorrection | None) -> datetime | None:
    if captured is None or value.basis not in ("source_raw", "hub_corrected"):
        return None
    if value.origin_mode not in ("LIVE", "REPLAY", "SIM"):
        return None
    try:
        raw = value.original_at
        if value.basis == "hub_corrected":
            raw -= timedelta(milliseconds=captured.offset_ms)
        if raw < captured.valid_from or (captured.valid_until is not None and raw >= captured.valid_until):
            return None
        return raw
    except OverflowError:
        return None


def normalized(
    value: TimeInput,
    raw: datetime,
    correction: ClockCorrection,
    *,
    now: datetime,
    future_tolerance_ms: int,
    maximum_uncertainty_ms: int,
) -> NormalizedTime:
    unknown = NormalizedTime(value.original_at, None, None, None, "unknown")
    if correction.uncertainty_ms > maximum_uncertainty_ms:
        return unknown
    try:
        corrected = raw + timedelta(milliseconds=correction.offset_ms)
        future_bound = now + timedelta(milliseconds=future_tolerance_ms)
    except OverflowError:
        return unknown
    state: Literal["known", "unknown", "future", "replay"] = (
        "replay" if value.origin_mode == "REPLAY" else "known"
    )
    if corrected > future_bound:
        state = "future"
    return NormalizedTime(
        value.original_at, corrected, correction.correction_id, correction.uncertainty_ms, state
    )


def fresh(value: NormalizedTime, *, now: datetime, window: timedelta) -> bool:
    require_utc(now)
    if (
        value.state != "known"
        or value.corrected_at is None
        or value.uncertainty_ms is None
        or value.uncertainty_ms < 0
        or window < timedelta(0)
    ):
        return False
    try:
        uncertainty = timedelta(milliseconds=value.uncertainty_ms)
        return now - window <= value.corrected_at - uncertainty and value.corrected_at + uncertainty <= now
    except OverflowError:
        return False


def instant(value: datetime) -> str:
    require_utc(value)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class ClockState:
    last_committed_at: datetime
    frozen: bool
    correction_version: int
    minimum_revision: int

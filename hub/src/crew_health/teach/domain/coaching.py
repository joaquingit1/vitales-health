"""CPR coaching sample derivation (CONTRACTS 23 "CPR coaching"; E30 3.7). A sample without a rate never carries a
number, a band or a direction (I30-14); a stale or uncertain raw sample is dropped, never shown as current."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

from crew_health.teach.domain.catalog import CompressionRateCheck
from crew_health.teach.domain.errors import InvalidValue

type NoRateReason = Literal["cannot_see_hands", "quality_gate_failed"]


@dataclass(frozen=True, slots=True)
class RawRateSample:
    captured_utc: datetime
    clock_uncertainty_ms: int
    rate: Fraction | None
    reason: NoRateReason | None


@dataclass(frozen=True, slots=True)
class DerivedSample:
    rate: Fraction | None
    reason: NoRateReason | None
    in_band: bool | None
    direction: Literal["too_slow", "too_fast"] | None
    phrase_key: Literal["ok", "too_slow", "too_fast", "cannot_see"]


def accept_raw(
    raw: RawRateSample,
    now: datetime,
    check: CompressionRateCheck,
    *,
    max_uncertainty_ms: int,
    last_captured: datetime | None,
) -> bool:
    """Ingest rule: drop samples older than one interval, too uncertain, from the future or not newer (3.7)."""
    age = now - raw.captured_utc
    if age < timedelta(0) or age > timedelta(seconds=check.update_interval_seconds):
        return False
    if raw.clock_uncertainty_ms > max_uncertainty_ms:
        return False
    return last_captured is None or raw.captured_utc > last_captured


def derive(raw: RawRateSample, check: CompressionRateCheck) -> DerivedSample:
    if (raw.rate is None) == (raw.reason is None):
        raise InvalidValue("rate_xor_reason")
    if raw.rate is None:
        return DerivedSample(None, raw.reason, None, None, "cannot_see")
    if raw.rate <= 0:
        raise InvalidValue("rate_not_positive")
    if raw.rate < check.target_min:
        return DerivedSample(raw.rate, None, False, "too_slow", "too_slow")
    if raw.rate > check.target_max:
        return DerivedSample(raw.rate, None, False, "too_fast", "too_fast")
    return DerivedSample(raw.rate, None, True, None, "ok")

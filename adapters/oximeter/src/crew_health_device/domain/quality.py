"""Device plausibility only; clinical acceptance belongs to the hub."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .profile import ComponentRule


@dataclass(frozen=True, slots=True)
class Sample:
    value: Decimal
    received_at: datetime
    monotonic_ns: int
    # Device evidence kept with the value (review minor 3): the frame's own timestamp and clock-set flag.
    device_time: str | None = None
    device_clock_set: bool | None = None


@dataclass(frozen=True, slots=True)
class Accepted:
    value: Decimal
    observed_at: datetime
    sample_count: int
    sample: Sample | None = None


@dataclass(frozen=True, slots=True)
class Refused:
    reason: Literal[
        "out_of_device_range",
        "saturated",
        "implausible_rate",
        "stuck",
        "insufficient_samples",
        "unknown_time_order",
        "not_steady",
    ]


Verdict = Accepted | Refused


def assess(
    samples: Sequence[Sample], rule: ComponentRule, min_samples: int, *, steady: bool = False
) -> Verdict:
    """Range, saturation and rate checks on every sample, then the reported sample: the last one, or with
    `steady` the last one equal to its predecessor. Never an average. A stuck sensor is decided from frame
    evidence (a repeated device-timestamped measurement, acquisition.py), not from equal values: a steady value is
    what a good reading looks like (C08)."""
    if any(not sample.value.is_finite() for sample in samples):
        return Refused("out_of_device_range")
    if rule.range_min is not None and rule.range_max is not None:
        if any(not rule.range_min <= sample.value <= rule.range_max for sample in samples):
            return Refused("out_of_device_range")
    for sample in samples:
        if rule.saturation_low is not None and sample.value <= rule.saturation_low:
            return Refused("saturated")
        if rule.saturation_high is not None and sample.value >= rule.saturation_high:
            return Refused("saturated")
    for previous, current in zip(samples, samples[1:], strict=False):
        delta_ns = current.monotonic_ns - previous.monotonic_ns
        if delta_ns <= 0:
            return Refused("unknown_time_order")
        if rule.max_rate_per_second is not None:
            if abs(current.value - previous.value) * Decimal(
                1_000_000_000
            ) > rule.max_rate_per_second * Decimal(delta_ns):
                return Refused("implausible_rate")
    if not samples or len(samples) < min_samples:
        return Refused("insufficient_samples")
    if not steady:
        return Accepted(samples[-1].value, samples[-1].received_at, len(samples), samples[-1])
    for index in range(len(samples) - 1, 0, -1):
        if samples[index].value == samples[index - 1].value:
            return Accepted(samples[index].value, samples[index].received_at, len(samples), samples[index])
    return Refused("not_steady")

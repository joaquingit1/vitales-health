"""Ports, shared value types, named operational settings and the plausibility guard of the habitat adapter.

Units travel with values: a `Quantity` is a Decimal and its wire unit. Time is injected through `Clock`; nothing
here reads the wall clock. Operational bounds are the E23 section 11 `habitat_adapter.*` settings (S5.11), never a
configuration document.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from crew_health_adapter_habitat.errors import SettingsInvalid

Origin = Literal["LIVE", "REPLAY", "SIM"]
Quality = Literal["accepted", "rejected", "uncertain", "not_assessed"]
ClockState = Literal["synchronized", "unsynchronized", "unknown"]

# E23 section 11: seed and maximum of each setting; the allowed range is 1..seed. batch_max_readings is the hub's
# sensors.batch_max_readings, which bounds one ingestReadings body.
SETTING_SEEDS: Mapping[str, int] = {
    "habitat_adapter.queue_max": 100000,
    "habitat_adapter.row_max_bytes": 65536,
    "habitat_adapter.http_timeout_s": 5,
    "habitat_adapter.retry_max": 5,
    "habitat_adapter.retry_max_backoff_s": 60,
    "habitat_adapter.batch_interval_s": 5,
    "sensors.batch_max_readings": 1000,
}


@dataclass(frozen=True, slots=True)
class Settings:
    queue_max: int = 100000
    row_max_bytes: int = 65536
    http_timeout_s: int = 5
    retry_max: int = 5
    retry_max_backoff_s: int = 60
    batch_interval_s: int = 5
    batch_max_readings: int = 1000

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> Settings:
        """Validate every key; an unknown key or a value outside 1..seed rejects the whole mapping."""
        unknown = sorted(set(values) - set(SETTING_SEEDS))
        if unknown:
            raise SettingsInvalid("unknown key " + unknown[0])
        chosen: dict[str, int] = {}
        for key, seed in sorted(SETTING_SEEDS.items()):
            raw = values.get(key, seed)
            if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= seed:
                raise SettingsInvalid(key + " must be an integer from 1 to " + str(seed))
            chosen[key] = raw
        return cls(
            queue_max=chosen["habitat_adapter.queue_max"],
            row_max_bytes=chosen["habitat_adapter.row_max_bytes"],
            http_timeout_s=chosen["habitat_adapter.http_timeout_s"],
            retry_max=chosen["habitat_adapter.retry_max"],
            retry_max_backoff_s=chosen["habitat_adapter.retry_max_backoff_s"],
            batch_interval_s=chosen["habitat_adapter.batch_interval_s"],
            batch_max_readings=chosen["sensors.batch_max_readings"],
        )


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class ReplayInfo:
    dataset: str
    time_basis: str


@dataclass(frozen=True, slots=True)
class CabinPressure:
    value: Decimal
    basis: Literal["measured", "assumed"]


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One parsed input row: the original value, unit and observation time. Never converted, never filled."""

    channel_id: str
    quantity: Quantity
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SourceItem:
    """What a source yields: a parsed row or a rejection code, with the source position it consumed."""

    position: int
    row: SourceRow | None
    reject_code: str | None = None


class ReadingSource(Protocol):
    def items(self, after: int) -> Iterator[SourceItem]:
        """Yield items with position greater than `after`, in source order, each position once."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def state(self) -> ClockState:
        """From the host's time service. `unknown` when it cannot say: never guessed."""
        ...


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class Criteria:
    """Sourced plausibility criteria in the wire unit. A criterion that is None is not checked."""

    basis: str
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    saturation: tuple[Decimal, ...] = ()
    stuck_count: int | None = None
    stuck_span_s: Decimal | None = None
    max_rate_per_s: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    quality: Quality
    flags: tuple[str, ...] = ()


@dataclass(slots=True)
class Guard:
    """Range, saturation, stuck value and rate-of-change checks of AGENTS 2, with only sourced criteria.

    Without criteria every reading is `not_assessed`: it is captured with its provenance and flagged, and no
    threshold is invented. Insufficient history cannot prove a stuck or rate check passed, so such a reading is
    `uncertain`. A failed check gives `rejected`; the reading is still captured, never dropped.
    """

    criteria: Criteria | None
    history: deque[tuple[Decimal, datetime]] = field(default_factory=lambda: deque(maxlen=1))

    def __post_init__(self) -> None:
        count = 1 if self.criteria is None or self.criteria.stuck_count is None else self.criteria.stuck_count
        self.history = deque(maxlen=max(count, 2))

    def assess(self, value: Decimal, at: datetime) -> Verdict:
        if self.criteria is None:
            return Verdict("not_assessed", ("NO_CRITERIA",))
        failed = _static_failures(self.criteria, value)
        failed += self._dynamic_failures(self.criteria, value, at)
        unproven = self._unproven(self.criteria, at)
        self.history.append((value, at))
        if failed:
            return Verdict("rejected", tuple(failed))
        return Verdict("uncertain", tuple(unproven)) if unproven else Verdict("accepted")

    def _dynamic_failures(self, criteria: Criteria, value: Decimal, at: datetime) -> list[str]:
        found: list[str] = []
        if criteria.max_rate_per_s is not None and self.history:
            last_value, last_at = self.history[-1]
            seconds = Decimal(str((at - last_at).total_seconds()))
            if seconds > 0 and abs(value - last_value) / seconds > criteria.max_rate_per_s:
                found.append("RATE_EXCEEDED")
        if criteria.stuck_count is not None and criteria.stuck_span_s is not None:
            window = list(self.history)[-(criteria.stuck_count - 1) :] if criteria.stuck_count > 1 else []
            same = len(window) == criteria.stuck_count - 1 and all(v == value for v, _ in window)
            if same and Decimal(str((at - window[0][1]).total_seconds())) >= criteria.stuck_span_s:
                found.append("STUCK_VALUE")
        return found

    def _unproven(self, criteria: Criteria, at: datetime) -> list[str]:
        found: list[str] = []
        if criteria.max_rate_per_s is not None:
            if not self.history or (at - self.history[-1][1]).total_seconds() <= 0:
                found.append("RATE_UNPROVEN")
        if criteria.stuck_count is not None and len(self.history) < criteria.stuck_count - 1:
            found.append("STUCK_UNPROVEN")
        return found


def _static_failures(criteria: Criteria, value: Decimal) -> list[str]:
    found: list[str] = []
    if criteria.minimum is not None and value < criteria.minimum:
        found.append("BELOW_RANGE")
    if criteria.maximum is not None and value > criteria.maximum:
        found.append("ABOVE_RANGE")
    if value in criteria.saturation:
        found.append("SATURATED")
    return found


def parse_decimal(text: str) -> Decimal | None:
    """A finite Decimal or None. NaN, infinity and anything unparseable are refused, never replaced."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    if not value.is_finite() or not math.isfinite(float(value)):
        return None
    return value


def utc_text(moment: datetime) -> str:
    """RFC 3339 UTC with Z, keeping fractional seconds only when present."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    text = moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return text

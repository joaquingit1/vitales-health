from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

if TYPE_CHECKING:
    from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic_ns(self) -> int: ...
    def process_epoch(self) -> str: ...


@dataclass(frozen=True, slots=True, order=True)
class Hlc:
    physical_ms: int
    logical: int
    node_id: str
    incarnation_id: str


@dataclass(frozen=True, slots=True)
class ClockCorrection:
    correction_id: str
    source_clock_id: str
    segment_id: str
    version: int
    valid_from: datetime
    valid_until: datetime | None
    offset_ms: int
    uncertainty_ms: int
    recorded_at: datetime
    source: Literal["operator", "measured", "ground"]  # internal enum

    def __post_init__(self) -> None:
        if self.valid_from is not None:
            require_utc(self.valid_from)
        if self.valid_until is not None:
            require_utc(self.valid_until)
        if self.recorded_at is not None:
            require_utc(self.recorded_at)


@dataclass(frozen=True, slots=True)
class TimeInput:
    original_at: datetime
    source_clock_id: str
    segment_id: str
    source_clock_version: int
    basis: Literal["source_raw", "hub_corrected"]  # internal enum
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]  # internal enum

    def __post_init__(self) -> None:
        if self.original_at is not None:
            require_utc(self.original_at)


@dataclass(frozen=True, slots=True)
class NormalizedTime:
    original_at: datetime
    corrected_at: datetime | None
    correction_id: str | None
    uncertainty_ms: int | None
    state: Literal["known", "unknown", "future", "replay"]  # internal enum

    def __post_init__(self) -> None:
        if self.original_at is not None:
            require_utc(self.original_at)
        if self.corrected_at is not None:
            require_utc(self.corrected_at)


@dataclass(frozen=True, slots=True)
class ClockCorrected:
    event_type: ClassVar[str] = "clock.corrected"
    previous_offset_ms: int
    offset_ms: int
    detected_at: datetime
    clock_version: int
    source: Literal["operator", "measured", "ground"]  # internal enum

    def __post_init__(self) -> None:
        if self.detected_at is not None:
            require_utc(self.detected_at)


@dataclass(frozen=True, slots=True)
class ClockRegressed:
    event_type: ClassVar[str] = "clock.regressed"
    observed_at: datetime
    last_committed_at: datetime
    delta_ms: int

    def __post_init__(self) -> None:
        if self.observed_at is not None:
            require_utc(self.observed_at)
        if self.last_committed_at is not None:
            require_utc(self.last_committed_at)


@dataclass(frozen=True, slots=True)
class ClockSegment:
    source_clock_id: str
    physical_clock_id: str
    segment_id: str
    predecessor_id: str | None
    first_sequence: int
    capture_offset_version: int


class ClockSegments(Protocol):
    def open(self, uow: UnitOfWork, segment: ClockSegment) -> None: ...
    def for_sequence(
        self, snapshot: ReadSnapshot, *, source_clock_id: str, sequence: int
    ) -> ClockSegment | None: ...


class ClockCorrections(Protocol):
    def apply(self, uow: UnitOfWork, correction: ClockCorrection) -> ClockCorrected: ...
    def history(
        self, snapshot: ReadSnapshot, *, after_version: int, limit: int
    ) -> tuple[ClockCorrection, ...]: ...
    def normalize(self, snapshot: ReadSnapshot, value: TimeInput) -> NormalizedTime: ...
    def elapsed(self, snapshot: ReadSnapshot, start: TimeInput, end: TimeInput) -> timedelta | None: ...


class HlcClock(Protocol):
    def tick(self, uow: UnitOfWork) -> Hlc: ...
    def observe(self, uow: UnitOfWork, remote: Hlc) -> Hlc: ...


def require_utc(value: datetime) -> None:
    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.errors import ProblemError

    if value.utcoffset() != timedelta(0):
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)

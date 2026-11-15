"""Reading facts, eligibility and exact arithmetic helpers (E23 3.7, 3.8; S5.4, D12)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Literal

from crew_health.sensors.domain.channel import Origin
from crew_health.sensors.domain.errors import InvalidValue, WindowScanBound

QualityStatus = Literal["accepted", "rejected", "uncertain", "not_assessed"]
TimeState = Literal["known", "replay", "unknown", "future"]

WIRE_PLACES = Decimal("0.0001")  # CONTRACTS 13 step 2: canonical values stored to four decimal places
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICROSECOND = timedelta(microseconds=1)


def micros(at: datetime) -> int:
    """Exact integer microseconds since the Unix epoch."""
    return (at - EPOCH) // MICROSECOND


def arithmetic() -> Context:
    """Decimal precision 34, half even, trapping every non-finite result (D12, E23 3.7)."""
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def exact_sums() -> Context:
    """Wide context so that sums of 34 digit terms are exact and order independent."""
    return Context(
        prec=96,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


@contextmanager
def decimal_scope(context: Context) -> Iterator[None]:
    with localcontext(context):
        yield


def require_finite(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidValue("non finite value")
    return value


def round_wire(value: Decimal) -> Decimal:
    """Round once, half even, to four places. Used on the wire only, never inside a calculation."""
    require_finite(value)
    with localcontext(arithmetic()):
        return value.quantize(WIRE_PLACES, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class ReadingFact:
    reading_id: str
    channel_id: str
    sequence: int
    at: datetime | None
    receive_at: datetime | None
    time_state: TimeState
    value: Decimal
    quality_status: QualityStatus
    origin_mode: Origin
    segment_id: str | None

    def __post_init__(self) -> None:
        require_finite(self.value)
        for moment in (self.at, self.receive_at):
            if moment is not None and moment.utcoffset() != timedelta(0):
                raise InvalidValue("naive or non UTC instant")


@dataclass(frozen=True, slots=True)
class Instant:
    """One normalized instant of a channel. Two values mean two sources disagree at this instant."""

    at: datetime
    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        require_finite(self.low)
        require_finite(self.high)
        if self.low > self.high:
            raise InvalidValue("low above high")

    @property
    def conflict(self) -> bool:
        return self.low != self.high


def is_eligible(reading: ReadingFact, activated_at: datetime | None) -> bool:
    """A reading takes part in windows only with a known time, an unrejected quality and a mission.

    DE23-13: in the active environment a reading before activation is never current, never shown and
    never in a window; LIVE and SIM use normalized observed time, REPLAY uses receive time.
    """
    if activated_at is None or reading.quality_status == "rejected":
        return False
    if reading.time_state not in ("known", "replay") or reading.at is None:
        return False
    basis = reading.receive_at if reading.origin_mode == "REPLAY" else reading.at
    return basis is not None and basis >= activated_at


def collapse_instants(
    readings: Sequence[ReadingFact], *, activated_at: datetime | None, max_rows: int
) -> tuple[Instant, ...]:
    """Eligible readings grouped by normalized instant, ascending. Same instant counts once."""
    if len(readings) > max_rows:
        raise WindowScanBound("reading rows above bound")
    low: dict[datetime, Decimal] = {}
    high: dict[datetime, Decimal] = {}
    for reading in readings:
        if not is_eligible(reading, activated_at) or reading.at is None:
            continue
        low[reading.at] = min(low.get(reading.at, reading.value), reading.value)
        high[reading.at] = max(high.get(reading.at, reading.value), reading.value)
    return tuple(Instant(at, low[at], high[at]) for at in sorted(low))


def latest_instant(instants: Sequence[Instant]) -> datetime | None:
    """E: the latest eligible observation (CONTRACTS 13). Never moves backward on late arrival."""
    return max((i.at for i in instants), default=None)


def advance_window_end(previous: datetime | None, candidate: datetime | None) -> datetime | None:
    """E only moves forward: a late, duplicate or looped row cannot pull it back."""
    if previous is None:
        return candidate
    if candidate is None:
        return previous
    return max(previous, candidate)

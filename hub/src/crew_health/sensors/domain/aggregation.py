"""Window aggregation over normalized instants (CONTRACTS 13, E23 3.7).

The window is the half open interval (E - W, E]. Nothing is interpolated and an empty window has no value.
Two readings that disagree at one instant give two variants (lower and higher value at every conflicting
instant); every aggregation is monotone in each value, so the two variants bound every possible choice.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import Protocol

from crew_health.sensors.domain.channel import AggregationSpec
from crew_health.sensors.domain.errors import ConfigInconsistent, SensorComputationFailure, WindowScanBound
from crew_health.sensors.domain.reading import (
    Instant,
    arithmetic,
    decimal_scope,
    exact_sums,
    micros,
)

Transform = Callable[[Decimal], Decimal]
_TEN = Decimal(10)


@dataclass(frozen=True, slots=True)
class WindowStats:
    kind: str
    window_start: datetime | None
    window_end: datetime
    low: Decimal
    high: Decimal
    n: int
    coverage: Decimal | None
    first_at: datetime


@dataclass(frozen=True, slots=True)
class Totals:
    """Partial sums of one window. Bucketed and direct evaluation both reduce to this."""

    n: int
    first_at: datetime
    last_at: datetime
    first_low: Decimal
    first_high: Decimal
    sum_low: Decimal
    sum_high: Decimal
    hold_low: Decimal
    hold_high: Decimal


class WindowSource(Protocol):
    def stats(self, spec: AggregationSpec, end: datetime) -> WindowStats | None: ...


def energy(value: Decimal) -> Decimal:
    """10^(L/10) for decibel levels. Non finite or overflowing results are a typed computation failure."""
    try:
        with decimal_scope(arithmetic()):
            result = _TEN ** (value / _TEN)
    except (InvalidOperation, Overflow, DivisionByZero) as error:
        raise SensorComputationFailure("energy overflow") from error
    return _finite_or_fail(result)


def identity(value: Decimal) -> Decimal:
    return value


def transform_for(spec: AggregationSpec) -> Transform:
    return energy if spec.kind == "energy_mean" else identity


def _finite_or_fail(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise SensorComputationFailure("non finite result")
    return value


def window_start(end: datetime, window_seconds: int) -> datetime:
    return end - timedelta(seconds=window_seconds)


def window_instants(instants: Sequence[Instant], end: datetime, window_seconds: int) -> tuple[Instant, ...]:
    start = window_start(end, window_seconds)
    return tuple(i for i in instants if start < i.at <= end)


def coverage(
    n: int,
    *,
    first_at: datetime,
    last_at: datetime,
    window_seconds: int,
    expected_interval_seconds: int | None,
) -> Decimal:
    """CONTRACTS 13: min(1, n x I / W); with no interval, (last - first) / W and 0 below two readings."""
    with decimal_scope(arithmetic()):
        if expected_interval_seconds is not None:
            return min(Decimal(1), Decimal(n * expected_interval_seconds) / Decimal(window_seconds))
        if n < 2:
            return Decimal(0)
        span = Decimal(micros(last_at) - micros(first_at))
        return min(Decimal(1), span / (Decimal(window_seconds) * 1_000_000))


def totals_of(points: Sequence[Instant], end: datetime, transform: Transform) -> Totals:
    """Reduce an ordered, nonempty run of instants. Each instant holds until the next, the last until end."""
    low = Decimal(0)
    high = Decimal(0)
    hold_low = Decimal(0)
    hold_high = Decimal(0)
    for index, point in enumerate(points):
        following = points[index + 1].at if index + 1 < len(points) else end
        dt = Decimal(micros(following) - micros(point.at))
        with decimal_scope(exact_sums()):
            low += point.low
            high += point.high
            hold_low += transform(point.low) * dt
            hold_high += transform(point.high) * dt
    first, last = points[0], points[-1]
    return Totals(len(points), first.at, last.at, first.low, first.high, low, high, hold_low, hold_high)


def _mean(total: Decimal, n: int) -> Decimal:
    with decimal_scope(arithmetic()):
        return total / Decimal(n)


def _held(hold: Decimal, covered_us: int, first_value: Decimal, kind: str) -> Decimal:
    if covered_us == 0:
        return first_value  # one reading exactly at E: its own value, no time to weight
    with decimal_scope(arithmetic()):
        mean = hold / Decimal(covered_us)
        if kind == "energy_mean":
            try:
                return _TEN * mean.log10()
            except (InvalidOperation, Overflow, DivisionByZero) as error:
                raise SensorComputationFailure("log10 of non positive mean") from error
        return mean


def value_of(kind: str, totals: Totals, end: datetime, *, high: bool) -> Decimal:
    """The aggregate of one variant from its partial sums (mean, time weighted, energy, sum)."""
    total = totals.sum_high if high else totals.sum_low
    if kind == "mean":
        result = _mean(total, totals.n)
    elif kind == "sum":
        result = total
    elif kind in ("time_weighted_mean", "energy_mean"):
        hold = totals.hold_high if high else totals.hold_low
        first = totals.first_high if high else totals.first_low
        result = _held(hold, micros(end) - micros(totals.first_at), first, kind)
    else:
        raise ConfigInconsistent("aggregation kind " + kind)
    return _finite_or_fail(result)


def stats_from_totals(
    spec: AggregationSpec, end: datetime, totals: Totals, *, expected_interval_seconds: int | None
) -> WindowStats:
    window = _window_seconds(spec)
    return WindowStats(
        kind=spec.kind,
        window_start=window_start(end, window),
        window_end=end,
        low=value_of(spec.kind, totals, end, high=False),
        high=value_of(spec.kind, totals, end, high=True),
        n=totals.n,
        coverage=coverage(
            totals.n,
            first_at=totals.first_at,
            last_at=totals.last_at,
            window_seconds=window,
            expected_interval_seconds=expected_interval_seconds,
        ),
        first_at=totals.first_at,
    )


def _window_seconds(spec: AggregationSpec) -> int:
    if spec.window_seconds is None or spec.window_seconds < 1:
        raise ConfigInconsistent("windowed aggregation without a window: " + spec.id)
    return spec.window_seconds


def latest_stats(instants: Sequence[Instant], end: datetime) -> WindowStats | None:
    """`latest`: the reading at E. It has no coverage and no window (CONTRACTS 13)."""
    for point in instants:
        if point.at == end:
            return WindowStats("latest", None, end, point.low, point.high, 1, None, end)
    return None


def aggregate_direct(
    spec: AggregationSpec,
    end: datetime,
    instants: Sequence[Instant],
    *,
    expected_interval_seconds: int | None,
    scan_max: int,
) -> WindowStats | None:
    """Scan the whole window. `max` and `sum` have no partial sums, so they are bounded by scan_max."""
    if spec.kind == "latest":
        return latest_stats(instants, end)
    points = window_instants(instants, end, _window_seconds(spec))
    if not points:
        return None
    if spec.kind in ("max", "sum") and len(points) > scan_max:
        raise WindowScanBound(spec.id)
    if spec.kind == "max":
        return _max_stats(spec, end, points, expected_interval_seconds)
    totals = totals_of(points, end, transform_for(spec))
    return stats_from_totals(spec, end, totals, expected_interval_seconds=expected_interval_seconds)


def _max_stats(
    spec: AggregationSpec, end: datetime, points: Sequence[Instant], expected_interval_seconds: int | None
) -> WindowStats:
    window = _window_seconds(spec)
    return WindowStats(
        kind="max",
        window_start=window_start(end, window),
        window_end=end,
        low=max(p.low for p in points),
        high=max(p.high for p in points),
        n=len(points),
        coverage=coverage(
            len(points),
            first_at=points[0].at,
            last_at=points[-1].at,
            window_seconds=window,
            expected_interval_seconds=expected_interval_seconds,
        ),
        first_at=points[0].at,
    )


@dataclass(frozen=True, slots=True)
class DirectWindows:
    """The reference WindowSource: scans instants it was given. Bucketed windows must equal it."""

    instants: tuple[Instant, ...]
    expected_interval_seconds: int | None
    scan_max: int

    def stats(self, spec: AggregationSpec, end: datetime) -> WindowStats | None:
        return aggregate_direct(
            spec,
            end,
            self.instants,
            expected_interval_seconds=self.expected_interval_seconds,
            scan_max=self.scan_max,
        )

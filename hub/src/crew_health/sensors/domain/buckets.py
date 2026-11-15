"""Bucketed partial sums and the bucketed window source (E23 3.7 "Incremental state").

A bucket k covers (k*B, (k+1)*B], the same half open shape as a window, so whole buckets tile a window and only
its two edges are scanned. A bucket holds the partial sums of the instants that start inside it, and an instant's
hold segment (until the next instant) belongs to the bucket where it starts. A late reading therefore touches
only its own bucket and the bucket of its predecessor. Persistence is the application layer's; these are pure.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from crew_health.sensors.domain.aggregation import (
    Totals,
    Transform,
    WindowStats,
    aggregate_direct,
    energy,
    identity,
    latest_stats,
    stats_from_totals,
    totals_of,
)
from crew_health.sensors.domain.channel import AggregationSpec
from crew_health.sensors.domain.errors import ConfigInconsistent, WindowScanBound
from crew_health.sensors.domain.reading import EPOCH, Instant, decimal_scope, exact_sums, micros

BucketTransform = Literal["identity", "energy"]
_MICROS_PER_SECOND = 1_000_000


@dataclass(frozen=True, slots=True)
class BucketSpec:
    seconds: int
    transform: BucketTransform

    def __post_init__(self) -> None:
        if self.seconds < 1:
            raise ConfigInconsistent("bucket seconds below one")


@dataclass(frozen=True, slots=True)
class BucketPartial:
    index: int
    totals: Totals


class BucketStore(Protocol):
    def partials(self, spec: BucketSpec, first: int, last: int, limit: int) -> Sequence[BucketPartial]: ...


class EdgeReader(Protocol):
    def instants(self, after: datetime, until: datetime, limit: int) -> Sequence[Instant]:
        """Instants with after < at <= until, ascending, at most `limit` of them."""
        ...


def bucket_spec_for(spec: AggregationSpec, bucket_seconds: int) -> BucketSpec | None:
    if spec.kind in ("mean", "time_weighted_mean"):
        return BucketSpec(bucket_seconds, "identity")
    if spec.kind == "energy_mean":
        return BucketSpec(bucket_seconds, "energy")
    return None


def _transform(spec: BucketSpec) -> Transform:
    return energy if spec.transform == "energy" else identity


def bucket_index(at: datetime, seconds: int) -> int:
    return (micros(at) - 1) // (seconds * _MICROS_PER_SECOND)


def _moment(microseconds: int) -> datetime:
    return EPOCH + timedelta(microseconds=microseconds)


def touched_by_insert(previous_at: datetime | None, at: datetime, seconds: int) -> frozenset[int]:
    """Buckets a new or late instant changes: its own and its predecessor's (whose hold segment shrinks)."""
    touched = {bucket_index(at, seconds)}
    if previous_at is not None:
        touched.add(bucket_index(previous_at, seconds))
    return frozenset(touched)


def build_buckets(
    instants: Sequence[Instant], spec: BucketSpec, *, only: Collection[int] | None = None
) -> dict[int, BucketPartial]:
    """Partial sums per bucket from the ordered full series, optionally only for the touched buckets."""
    transform = _transform(spec)
    groups: dict[int, list[tuple[Instant, datetime]]] = {}
    for position, point in enumerate(instants):
        index = bucket_index(point.at, spec.seconds)
        if only is not None and index not in only:
            continue
        following = instants[position + 1].at if position + 1 < len(instants) else point.at
        groups.setdefault(index, []).append((point, following))
    return {
        index: BucketPartial(index, _bucket_totals(members, transform)) for index, members in groups.items()
    }


def _bucket_totals(members: Sequence[tuple[Instant, datetime]], transform: Transform) -> Totals:
    low = Decimal(0)
    high = Decimal(0)
    hold_low = Decimal(0)
    hold_high = Decimal(0)
    for point, following in members:
        dt = Decimal(micros(following) - micros(point.at))
        with decimal_scope(exact_sums()):
            low += point.low
            high += point.high
            hold_low += transform(point.low) * dt
            hold_high += transform(point.high) * dt
    first, last = members[0][0], members[-1][0]
    return Totals(len(members), first.at, last.at, first.low, first.high, low, high, hold_low, hold_high)


def _leading_totals(lead: Sequence[Instant], next_at: datetime, transform: Transform) -> Totals:
    members = [(p, lead[i + 1].at if i + 1 < len(lead) else next_at) for i, p in enumerate(lead)]
    return _bucket_totals(members, transform)


def _merge(parts: Sequence[Totals]) -> Totals:
    with decimal_scope(exact_sums()):
        return Totals(
            n=sum(p.n for p in parts),
            first_at=parts[0].first_at,
            last_at=parts[-1].last_at,
            first_low=parts[0].first_low,
            first_high=parts[0].first_high,
            sum_low=sum((p.sum_low for p in parts), Decimal(0)),
            sum_high=sum((p.sum_high for p in parts), Decimal(0)),
            hold_low=sum((p.hold_low for p in parts), Decimal(0)),
            hold_high=sum((p.hold_high for p in parts), Decimal(0)),
        )


@dataclass(frozen=True, slots=True)
class BucketedWindows:
    """Window source reading whole buckets and scanning only the two edges. Equals DirectWindows.

    `end` must be E, the latest eligible instant: holds of the last whole bucket run to later instants.
    """

    bucket_seconds: int
    store: BucketStore
    reader: EdgeReader
    latest: Instant | None
    expected_interval_seconds: int | None
    scan_max: int
    max_buckets: int

    def stats(self, spec: AggregationSpec, end: datetime) -> WindowStats | None:
        if spec.kind == "latest":
            return latest_stats(() if self.latest is None else (self.latest,), end)
        if self.latest is None or end != self.latest.at:
            raise ConfigInconsistent("bucketed window must end at E")
        if spec.window_seconds is None:
            raise ConfigInconsistent("windowed aggregation without a window: " + spec.id)
        start = end - timedelta(seconds=spec.window_seconds)
        bucket = bucket_spec_for(spec, self.bucket_seconds)
        if bucket is None:
            return self._scanned(spec, start, end)
        return self._from_buckets(spec, bucket, start, end)

    def _scan(self, after: datetime, until: datetime) -> tuple[Instant, ...]:
        found = tuple(self.reader.instants(after, until, self.scan_max + 1))
        if len(found) > self.scan_max:
            raise WindowScanBound("edge or window scan above bound")
        return found

    def _scanned(self, spec: AggregationSpec, start: datetime, end: datetime) -> WindowStats | None:
        points = self._scan(start, end)
        return aggregate_direct(
            spec,
            end,
            points,
            expected_interval_seconds=self.expected_interval_seconds,
            scan_max=self.scan_max,
        )

    def _from_buckets(
        self, spec: AggregationSpec, bucket: BucketSpec, start: datetime, end: datetime
    ) -> WindowStats | None:
        width = self.bucket_seconds * _MICROS_PER_SECOND
        first = -(-micros(start) // width)
        last = micros(end) // width - 1
        if first > last:
            points = self._scan(start, end)
            return self._reduce(spec, end, [totals_of(points, end, _transform(bucket))] if points else [])
        if last - first + 1 > self.max_buckets:
            raise WindowScanBound("window buckets above bound")
        partials = sorted(self.store.partials(bucket, first, last, self.max_buckets), key=lambda p: p.index)
        lead = self._scan(start, _moment(first * width))
        trail = self._scan(_moment((last + 1) * width), end)
        parts: list[Totals] = []
        if lead:
            following = partials[0].totals.first_at if partials else (trail[0].at if trail else end)
            parts.append(_leading_totals(lead, following, _transform(bucket)))
        parts.extend(p.totals for p in partials)
        if trail:
            parts.append(totals_of(trail, end, _transform(bucket)))
        return self._reduce(spec, end, parts)

    def _reduce(self, spec: AggregationSpec, end: datetime, parts: Sequence[Totals]) -> WindowStats | None:
        if not parts:
            return None
        return stats_from_totals(
            spec, end, _merge(parts), expected_interval_seconds=self.expected_interval_seconds
        )

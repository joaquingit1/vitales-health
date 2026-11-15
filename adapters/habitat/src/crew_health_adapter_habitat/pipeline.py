"""The adapter loop: source item, plausibility guard, durable queue, bounded flush through the hub.

Source progress, the row and its sequence commit together (queue.py), so a crash never loses an accepted row or
reuses a sequence. A full queue pauses the source (health says queue_full) and resumes in order once the hub has
taken rows; it never drops one. A transport fault keeps every row queued and visible; the source keeps filling the
bounded queue until it is full.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Any

from crew_health_adapter_habitat.errors import CommandConflict, QueueFull, TransportFault
from crew_health_adapter_habitat.feeds import Feed
from crew_health_adapter_habitat.health import HealthState
from crew_health_adapter_habitat.http_client import Sender, validate_reading
from crew_health_adapter_habitat.ports import (
    Clock,
    Guard,
    Origin,
    ReadingSource,
    Settings,
    Sleeper,
    SourceItem,
    parse_decimal,
    utc_text,
)
from crew_health_adapter_habitat.queue import DurableQueue


@dataclass(frozen=True, slots=True)
class RunSummary:
    enqueued: int
    skipped: int
    paused: bool
    fault: str | None


def wire_row(
    feed: Feed, origin: Origin, sequence: int, value: Decimal, at: datetime, quality: str
) -> Mapping[str, Any]:
    """One AdapterReadingBatch row (generated Reading schema). Value and unit are the original, never converted."""
    number = float(value)
    row: dict[str, Any] = {
        "channel_id": feed.channel_id,
        "adapter_sequence": sequence,
        "value": number,
        "unit": feed.unit,
        "observed_at": utc_text(at),
        "origin_mode": origin,
        "quality_status": quality,
    }
    if feed.replay is not None:
        row["replay"] = {"dataset": feed.replay.dataset, "time_basis": feed.replay.time_basis}
    if feed.cabin_pressure is not None:
        row["cabin_pressure_mmhg"] = {
            "value": float(feed.cabin_pressure.value),
            "basis": feed.cabin_pressure.basis,
        }
    return row


class Pipeline:
    def __init__(
        self,
        feed: Feed,
        source: ReadingSource,
        queue: DurableQueue,
        sender: Sender,
        health: HealthState,
        clock: Clock,
        sleeper: Sleeper,
        settings: Settings,
        origin: Origin,
    ) -> None:
        self.feed, self.source, self.queue, self.sender = feed, source, queue, sender
        self.health, self.clock, self.sleeper, self.settings, self.origin = (
            health,
            clock,
            sleeper,
            settings,
            origin,
        )
        self.guard = Guard(feed.criteria)
        self.enqueued = 0
        self.skipped = 0
        self.fault: str | None = None
        self.blocked_until: datetime | None = None
        self.last_flush = clock.now()
        self.previous_at: datetime | None = None

    def run(self, stop: Callable[[], bool]) -> RunSummary:
        paused = False
        try:
            for item in self.source.items(self.queue.progress(self.feed.feed_id)):
                if stop():
                    break
                self._pace(item)
                if not self._take(item):
                    paused = True
                    break
                self._maybe_flush()
                self.health.beat()
        finally:
            self._publish()
        self._drain()
        return RunSummary(self.enqueued, self.skipped, paused, self.fault)

    def _take(self, item: SourceItem) -> bool:
        if item.row is None:
            self.queue.skip(self.feed.feed_id, item.position, item.reject_code or "REJECTED")
            self.skipped += 1
            return True
        value = parse_decimal(str(item.row.quantity.value))
        if value is None:
            self.queue.skip(self.feed.feed_id, item.position, "BAD_VALUE")
            self.skipped += 1
            return True
        verdict = self.guard.assess(value, item.row.observed_at)
        at = item.row.observed_at
        if not validate_reading(wire_row(self.feed, self.origin, 0, value, at, verdict.quality)):
            self.queue.skip(self.feed.feed_id, item.position, "ROW_INVALID")
            self.skipped += 1
            return True
        for _ in range(self.settings.retry_max):
            try:
                self.queue.enqueue(
                    self.feed.channel_id,
                    lambda seq: wire_row(self.feed, self.origin, seq, value, at, verdict.quality),
                    feed_id=self.feed.feed_id,
                    position=item.position,
                )
            except QueueFull:
                self.health.update(queue_full=True)
                self._try_flush(force=True)
                self.sleeper.sleep(float(self.settings.batch_interval_s))
                self.health.beat()
                continue
            self.health.update(queue_full=False)
            self.enqueued += 1
            return True
        return False

    def _pace(self, item: SourceItem) -> None:
        pace, row = self.feed.pace, item.row
        if pace.mode == "fixed" and self.previous_at is not None:
            self.sleeper.sleep(pace.interval_s)
        elif pace.mode == "recorded" and row is not None and self.previous_at is not None:
            gap = (row.observed_at - self.previous_at).total_seconds() / pace.speedup
            self.sleeper.sleep(min(max(gap, 0.0), float(self.settings.retry_max_backoff_s)))
        if row is not None:
            self.previous_at = row.observed_at
        elif self.previous_at is None:
            self.previous_at = self.clock.now()

    def _maybe_flush(self) -> None:
        waiting = self.queue.depth()
        due = (self.clock.now() - self.last_flush).total_seconds() >= self.settings.batch_interval_s
        if waiting >= self.settings.batch_max_readings or due:
            self._try_flush(force=False)

    def _try_flush(self, *, force: bool) -> None:
        now = self.clock.now()
        if self.blocked_until is not None and now < self.blocked_until and not force:
            return
        try:
            self.sender.flush(1)
            self.fault = None
            self.blocked_until = None
            self.health.update(clear_fault=True)
        except CommandConflict as error:
            self._fault(error.code, None)
        except TransportFault as error:
            self._fault(error.code, now)
        self.last_flush = now

    def _fault(self, code: str, now: datetime | None) -> None:
        self.fault = code
        self.health.update(fault=code)
        if now is not None:
            self.blocked_until = now + timedelta(seconds=self.settings.retry_max_backoff_s)

    def _drain(self) -> None:
        batches = ceil(self.settings.queue_max / self.settings.batch_max_readings) + 1
        self._try_flush(force=True)
        for _ in range(batches):
            if self.fault is not None or self.queue.depth() == 0:
                break
            self._try_flush(force=True)
        self._publish()

    def _publish(self) -> None:
        self.health.set_counts(self.queue.counts())
        self.health.update(queue_full=self.queue.is_full())

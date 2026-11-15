"""SIM: generated inputs behind the same ports as REPLAY and LIVE (E23 5.2).

Three kinds, all named by a feed entry: frozen rows, a seeded generator, or the habitat simulated twin, whose
Bluetooth Environmental Sensing notifications or CCSDS packets go through the real `ble_ess` and `xtce` parsers.
Every SIM reading carries origin SIM. The twin declares itself simulated in its device identity; a sim feed that
reaches a device not declaring that is refused.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from crew_health_adapter_habitat.ble_ess import (
    BleLink,
    DeviceIdentity,
    EssSource,
    Notification,
    origin_of,
)
from crew_health_adapter_habitat.errors import FeedInvalid, SourceUnavailable
from crew_health_adapter_habitat.feeds import Feed
from crew_health_adapter_habitat.ports import (
    Clock,
    Quantity,
    ReadingSource,
    SourceItem,
    SourceRow,
    parse_decimal,
)
from crew_health_adapter_habitat.xtce import XtceSource, load_xtce
from crew_health_sim_habitat import EssTwin, LinkLost, PacketTwin, TwinError


class FrozenSource:
    """Frozen rows: a value and its offset in seconds from the run's epoch."""

    def __init__(self, feed: Feed, epoch: datetime) -> None:
        sim = _sim(feed)
        self.feed, self.epoch, self.rows = feed, epoch, sim["rows"]

    def items(self, after: int) -> Iterator[SourceItem]:
        for position in range(max(after, 0) + 1, len(self.rows) + 1):
            row = self.rows[position - 1]
            value = parse_decimal(str(row["value"]))
            if value is None:
                yield SourceItem(position, None, "BAD_VALUE")
                continue
            at = self.epoch + timedelta(seconds=float(row["offset_s"]))
            yield SourceItem(position, SourceRow(self.feed.channel_id, Quantity(value, self.feed.unit), at))


class GeneratorSource:
    """A seeded random walk. The same seed gives the same rows; every parameter is in the feed with its basis."""

    def __init__(self, feed: Feed, epoch: datetime) -> None:
        self.feed, self.epoch, self.sim = feed, epoch, _sim(feed)

    def items(self, after: int) -> Iterator[SourceItem]:
        rng = random.Random(int(self.sim["seed"]))
        value = float(self.sim["start"])
        for position in range(1, int(self.sim["count"]) + 1):
            value += rng.gauss(0.0, float(self.sim["step_sd"]))
            if position <= after:
                continue
            at = self.epoch + timedelta(seconds=position * float(self.sim["interval_s"]))
            decimal = parse_decimal(repr(round(value, 6)))
            if decimal is None:
                yield SourceItem(position, None, "BAD_VALUE")
            else:
                yield SourceItem(
                    position, SourceRow(self.feed.channel_id, Quantity(decimal, self.feed.unit), at)
                )


class TwinBleLink:
    """Adapts the twin's plain frames to the adapter's BleLink port."""

    def __init__(self, twin: EssTwin) -> None:
        self.twin = twin

    def identity(self) -> DeviceIdentity:
        found = self.twin.identity()
        return DeviceIdentity(found.name, found.serial, found.simulated)

    def notifications(self) -> Iterator[Notification]:
        try:
            for frame in self.twin.frames():
                yield Notification(frame.uuid16, frame.payload, frame.rssi_dbm)
        except LinkLost as error:
            raise SourceUnavailable("simulated device disconnected") from error

    def close(self) -> None:
        return None


def _sim(feed: Feed) -> Mapping[str, Any]:
    if feed.sim is None:
        raise FeedInvalid("sim feed needs a sim entry")
    return feed.sim


def _packets(twin: PacketTwin) -> Iterator[bytes]:
    try:
        yield from twin.packets()
    except LinkLost as error:
        raise SourceUnavailable("simulated device disconnected") from error


def _twin_source(feed: Feed, clock: Clock) -> ReadingSource:
    sim = _sim(feed)
    data_dir = Path(sim["data_dir"])
    try:
        if sim["protocol"] == "ble_ess":
            link: BleLink = TwinBleLink(
                EssTwin(int(sim["select"], 16), sim["scenario"], data_dir, int(sim["seed"]))
            )
            if origin_of(link.identity(), None) != "SIM":
                raise FeedInvalid("a sim feed reached a device that does not declare itself simulated")
            return EssSource(link, feed.channel_id, int(sim["select"], 16), clock)
        twin = PacketTwin(sim["select"], sim["scenario"], data_dir)
        if not twin.identity().simulated:
            raise FeedInvalid("a sim feed reached a device that does not declare itself simulated")
        return XtceSource(
            load_xtce(twin.xtce_document()), _packets(twin), feed.channel_id, sim["select"], clock
        )
    except TwinError as error:
        raise FeedInvalid("twin refused: " + str(error)) from error
    except ValueError as error:
        raise FeedInvalid("twin characteristic is not a hex number") from error


def build_sim_source(feed: Feed, clock: Clock, epoch: datetime) -> ReadingSource:
    kind = _sim(feed)["type"]
    if kind == "frozen":
        return FrozenSource(feed, epoch)
    if kind == "generator":
        return GeneratorSource(feed, epoch)
    return _twin_source(feed, clock)


__all__ = ["FrozenSource", "GeneratorSource", "TwinBleLink", "build_sim_source"]

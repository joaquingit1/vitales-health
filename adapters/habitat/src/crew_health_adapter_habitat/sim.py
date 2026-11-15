"""SIM: generated inputs behind the same ports as REPLAY and LIVE (E23 5.2).

Three kinds, all named by a feed entry: frozen rows, a seeded generator, or the habitat simulated twins of
`simulators/habitat`, driven by its simulated devices. The Bluetooth Environmental Sensing twin is used through
its BleakClient-like surface (`ess_client_factory`: connect, start_notify, the disconnected callback), and its
bytes go through the real `ble_ess` parser. The CCSDS packet twin builds an XTCE document and space packets for
the real `xtce` parser. Every SIM reading carries origin SIM. A twin declares itself simulated; a sim feed that
reaches a device not declaring that is refused.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
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
from crew_health_sim_habitat.ble_ess import (
    EssConfig,
    EssFault,
    EssLinkError,
    EssTwin,
    check_sources,
    ess_client_factory,
    full_uuid,
    load_ess_config,
)
from crew_health_sim_habitat.devicecfg import DeviceKind, load_device_kinds
from crew_health_sim_habitat.errors import SimulatorError
from crew_health_sim_habitat.packets import (
    PacketConfig,
    PacketTwin,
    check_packet_sources,
    load_packet_config,
)
from crew_health_sim_habitat.runner import Tick, build_devices, simulate
from crew_health_sim_habitat.scenariocfg import Scenario, load_scenarios
from crew_health_sim_habitat.wiring import Wiring, load_wiring


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


@dataclass(frozen=True, slots=True)
class TwinWorld:
    """The simulator documents a twin feed needs: loaded and cross-checked once, when the source is built."""

    kinds: Mapping[str, DeviceKind]
    scenarios: Mapping[str, Scenario]
    wiring: Wiring
    ess: EssConfig
    packets: PacketConfig

    def run(self, scenario_id: str) -> tuple[Scenario, tuple[EssFault, ...]]:
        """The base scenario to simulate and the byte-level faults of an ESS fault scenario, if one is named."""
        ess = self.ess.scenarios.get(scenario_id)
        if ess is None:
            if scenario_id not in self.scenarios:
                raise FeedInvalid("twin scenario is unknown: " + scenario_id)
            return self.scenarios[scenario_id], ()
        return replace(self.scenarios[ess.base_scenario], duration_s=ess.duration_s), ess.faults

    def ticks(self, scenario: Scenario) -> Iterator[Tick]:
        return simulate(scenario, build_devices(scenario, self.wiring, self.kinds))


def load_twin_world(config_dir: Path) -> TwinWorld:
    kinds = load_device_kinds(config_dir / "devices.json")
    wiring = load_wiring(config_dir / "feeds.json")
    ess = load_ess_config(config_dir / "ble-ess.json")
    packets = load_packet_config(config_dir / "ccsds-packets.json")
    units = {(d.device_id, o.name): o.unit for d in wiring.devices for o in kinds[d.kind].outputs}
    check_sources(ess, units)
    check_packet_sources(packets, units)
    return TwinWorld(kinds, load_scenarios(config_dir / "scenarios.json"), wiring, ess, packets)


class TwinBleLink:
    """The BLE ESS twin behind the adapter's BleLink port, used as a BleakClient: the client is connected,
    notifications are started, and its disconnected callback reports a lost link.

    The simulated devices drive the twin tick by tick. After a lost link, the next `notifications()` call is a
    reconnect: it waits (ticks pass, nothing is sent) until the twin accepts a connection, so a gap stays a gap.
    """

    def __init__(
        self, config: EssConfig, faults: tuple[EssFault, ...], ticks: Iterable[Tick], uuids: tuple[int, ...]
    ) -> None:
        self.uuids = uuids
        self.lost: list[EssTwin] = []
        self.inbox: deque[Notification] = deque()  # at most one notification per characteristic per tick
        self.ticks = iter(ticks)
        self.twin = ess_client_factory(config, faults)(config.twin_id, self.lost.append)

    def identity(self) -> DeviceIdentity:
        return DeviceIdentity("BLE ESS twin " + self.twin.address, self.twin.address, self.twin.simulated)

    def _on_notify(self, uuid: str, data: bytearray) -> None:
        self.inbox.append(Notification(int(uuid[4:8], 16), bytes(data)))

    async def _subscribe(self) -> None:
        await self.twin.connect()
        for uuid16 in self.uuids:
            await self.twin.start_notify(full_uuid(uuid16), self._on_notify)

    def _reconnect(self) -> None:
        while True:
            try:
                asyncio.run(self._subscribe())
                return
            except EssLinkError:
                tick = next(self.ticks, None)
                if tick is None:
                    raise SourceUnavailable("simulated device stayed out of reach") from None
                self.twin.advance(tick.t_s, tick.readings)

    def notifications(self) -> Iterator[Notification]:
        if not self.twin.is_connected:
            self._reconnect()
        for tick in self.ticks:
            self.twin.advance(tick.t_s, tick.readings)
            while self.inbox:
                yield self.inbox.popleft()
            if self.lost:
                self.lost.clear()
                raise SourceUnavailable("simulated device disconnected")

    def close(self) -> None:
        asyncio.run(self.twin.disconnect())


def _sim(feed: Feed) -> Mapping[str, Any]:
    if feed.sim is None:
        raise FeedInvalid("sim feed needs a sim entry")
    return feed.sim


def _twin_source(feed: Feed, clock: Clock) -> ReadingSource:
    sim = _sim(feed)
    try:
        world = load_twin_world(Path(sim["config_dir"]))
        scenario, faults = world.run(sim["scenario"])
        if sim["protocol"] == "ble_ess":
            uuid16 = int(sim["select"], 16)
            link: BleLink = TwinBleLink(world.ess, faults, world.ticks(scenario), (uuid16,))
            if origin_of(link.identity(), None) != "SIM":
                raise FeedInvalid("a sim feed reached a device that does not declare itself simulated")
            return EssSource(link, feed.channel_id, uuid16, clock)
        twin = PacketTwin(world.packets, sim["select"])
        if not twin.simulated:
            raise FeedInvalid("a sim feed reached a device that does not declare itself simulated")
        return XtceSource(
            load_xtce(twin.xtce_document()), twin.packets(world.ticks(scenario)), feed.channel_id,
            sim["select"], clock,
        )  # fmt: skip
    except SimulatorError as error:
        raise FeedInvalid("twin refused: " + str(error)) from error
    except (ValueError, KeyError) as error:
        raise FeedInvalid("twin entry is not usable: " + type(error).__name__) from error


def build_sim_source(feed: Feed, clock: Clock, epoch: datetime) -> ReadingSource:
    kind = _sim(feed)["type"]
    if kind == "frozen":
        return FrozenSource(feed, epoch)
    if kind == "generator":
        return GeneratorSource(feed, epoch)
    return _twin_source(feed, clock)


__all__ = [
    "FrozenSource",
    "GeneratorSource",
    "TwinBleLink",
    "TwinWorld",
    "build_sim_source",
    "load_twin_world",
]

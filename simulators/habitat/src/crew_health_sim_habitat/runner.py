"""Runs a scenario: steps the cabin, samples the devices, and hands mapped readings to a feeder."""

import math
import random
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from crew_health_sim_habitat.devicecfg import DeviceKind
from crew_health_sim_habitat.devices import Device, DeviceReading
from crew_health_sim_habitat.errors import ConfigError
from crew_health_sim_habitat.feeder import Feeder
from crew_health_sim_habitat.payload import WireReading
from crew_health_sim_habitat.scenariocfg import Scenario
from crew_health_sim_habitat.units import MMHG_PER_KPA
from crew_health_sim_habitat.wiring import Binding, Wiring, bindings_by_output
from crew_health_sim_habitat.world import CabinWorld, Truth

SIM_ORIGIN = "SIM"
PRESSURE_BASIS_MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class Tick:
    t_s: float
    truth: Truth
    readings: tuple[DeviceReading, ...]


def build_devices(scenario: Scenario, wiring: Wiring, kinds: Mapping[str, DeviceKind]) -> list[Device]:
    devices = []
    for instance in wiring.devices:
        rng = random.Random(f"{scenario.seed}:{instance.device_id}")
        devices.append(Device(instance.device_id, kinds[instance.kind], scenario.faults, rng))
    return devices


def simulate(scenario: Scenario, devices: Sequence[Device], step_s: float = 1.0) -> Iterator[Tick]:
    """Yield every tick that has at least one device sample, for the scenario's duration."""
    if step_s <= 0 or any(not float(d.kind.interval_s / step_s).is_integer() for d in devices):
        raise ConfigError("every device interval must be a whole number of steps")
    world = CabinWorld(scenario, random.Random(f"{scenario.seed}:world"))
    steps = math.floor(scenario.duration_s / step_s)
    for index in range(steps + 1):
        t_s = index * step_s
        due = [d for d in devices if d.due(t_s)]
        if due:
            truth = world.truth()
            readings = tuple(r for d in due for r in d.sample(t_s, truth))
            yield Tick(t_s, truth, readings)
        world.step(step_s)


@dataclass(slots=True)
class RunReport:
    ticks: int = 0
    readings: int = 0
    offered: int = 0
    unmapped: Counter[str] = field(default_factory=Counter)
    skipped_queue_full: int = 0
    missing_pressure: int = 0


class WireMapper:
    """Turns device readings into wire readings for the channels they are bound to."""

    def __init__(
        self, wiring: Wiring, start_at: datetime, devices: Sequence[Device], pressure_max_age_s: float
    ) -> None:
        self._bindings = bindings_by_output(wiring.bindings)
        self._quality = wiring.quality_status
        self._start_at = start_at
        self._intervals = {d.device_id: d.kind.interval_s for d in devices}
        self._max_age = pressure_max_age_s
        self._latest: dict[tuple[str, str], DeviceReading] = {}

    def observe(self, reading: DeviceReading) -> None:
        self._latest[(reading.device_id, reading.output)] = reading

    def binding(self, reading: DeviceReading) -> Binding | None:
        return self._bindings.get((reading.device_id, reading.output))

    def _pressure_mmhg(self, binding: Binding, t_s: float) -> float | None:
        if binding.pressure_device is None or binding.pressure_output is None:
            return None
        latest = self._latest.get((binding.pressure_device, binding.pressure_output))
        if latest is None or t_s - latest.t_s > self._max_age:
            return None
        return round(latest.quantity.value * MMHG_PER_KPA, 2)

    def to_wire(self, reading: DeviceReading, binding: Binding) -> WireReading:
        pressure = self._pressure_mmhg(binding, reading.t_s)
        window = None if binding.aggregation is None else int(self._intervals[reading.device_id])
        return WireReading(
            channel_id=binding.channel_id,
            quantity=reading.quantity,
            observed_at=self._start_at + timedelta(seconds=reading.t_s),
            origin_mode=SIM_ORIGIN,
            quality_status=self._quality[reading.status],
            aggregation=binding.aggregation,
            window_seconds=window,
            cabin_pressure_mmhg=pressure,
            cabin_pressure_basis=None if pressure is None else PRESSURE_BASIS_MEASURED,
        )


def wire_readings(mapper: WireMapper, tick: Tick, report: RunReport) -> list[WireReading]:
    """The tick's readings that have a hub channel, after recording every reading for pressure context."""
    for reading in tick.readings:
        mapper.observe(reading)
    wire: list[WireReading] = []
    for reading in tick.readings:
        binding = mapper.binding(reading)
        if binding is None:
            report.unmapped[f"{reading.device_id}.{reading.output}"] += 1
            continue
        item = mapper.to_wire(reading, binding)
        if binding.pressure_device is not None and item.cabin_pressure_mmhg is None:
            report.missing_pressure += 1
        wire.append(item)
    return wire


def feed_scenario(
    ticks: Iterator[Tick],
    mapper: WireMapper,
    feeder: Feeder,
    wait_until: Callable[[float], None],
    pumps_per_tick: int = 4,
    pump_at_pending: int = 1,
) -> RunReport:
    """Offer each tick's mapped readings; while the queue is full the devices are not read.

    pump_at_pending holds sending until that many readings wait, so a backfill sends full batches.
    """
    report = RunReport()
    for tick in ticks:
        wait_until(tick.t_s)
        report.ticks += 1
        report.readings += len(tick.readings)
        wire = wire_readings(mapper, tick, report)
        if wire and feeder.free_slots < len(wire):
            report.skipped_queue_full += len(wire)
        else:
            for item in wire:
                if feeder.offer(item):
                    report.offered += 1
        for _ in range(pumps_per_tick):
            if feeder.pending < pump_at_pending or not feeder.pump():
                break
    return report

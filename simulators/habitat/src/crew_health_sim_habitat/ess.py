"""The Bluetooth Environmental Sensing twin: encodes recorded values into 0x181A characteristic notifications.

The encodings below are written out independently of the adapter's parser table, so a mistake on either side
fails the end-to-end test instead of cancelling out. Source: Bluetooth SIG GATT Specification Supplement
(non-NASA; NASA publishes no Bluetooth profile). The device declares itself simulated in its identity.
"""

from __future__ import annotations

import random
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from crew_health_sim_habitat.data import MAX_STEPS, TwinError, column, decimal, read_rows, scenario_table

# uuid16: (struct format, resolution, unknown code, saturation code)
ENCODINGS: dict[int, tuple[str, Decimal, int, int | None]] = {
    0x2A6E: ("<h", Decimal("0.01"), -32768, None),
    0x2A6F: ("<H", Decimal("0.01"), 0xFFFF, None),
    0x2B8C: ("<H", Decimal(1), 0xFFFF, 0xFFFE),
}


class LinkLost(Exception):
    """The simulated device disconnected."""


@dataclass(frozen=True, slots=True)
class Identity:
    name: str
    serial: str
    simulated: bool


@dataclass(frozen=True, slots=True)
class Frame:
    uuid16: int
    payload: bytes
    rssi_dbm: int | None


def _pack(uuid16: int, raw: int) -> bytes:
    fmt = ENCODINGS[uuid16][0]
    try:
        return struct.pack(fmt, raw)
    except struct.error as error:
        raise TwinError("raw value outside the characteristic's encoding") from error


def raw_of(uuid16: int, value: Decimal) -> int:
    resolution = ENCODINGS[uuid16][1]
    return int((value / resolution).to_integral_value(rounding=ROUND_HALF_EVEN))


def _recorded(table: dict[str, Any], uuid16: int, data_dir: Path) -> list[Decimal]:
    spec = table["ess_sources"].get(f"0x{uuid16:04X}")
    if spec is None:
        raise TwinError("no recorded source for this characteristic")
    return column(read_rows(data_dir / spec["file"]), spec["column"])


def _co2_case(scenario: dict[str, Any], data_dir: Path) -> list[Decimal]:
    """CO2 ppm shaped from the printed levels of the ISS CO2 symptom case, read from its CSV by line."""
    rows = {r["event"]: r for r in read_rows(data_dir / scenario["case_file"])}
    pressure = decimal(str(scenario["pressure_mmhg"]))
    levels: list[Decimal] = []
    last: Decimal | None = None
    for step in scenario["shape"]:
        if "literal_mm_hg" in step:
            target = decimal(str(step["literal_mm_hg"]))
        else:
            target = decimal(rows[step["event"]][step["column"]])
        ppm = target / pressure * Decimal(1_000_000)
        ramp = int(step.get("ramp_samples", 0))
        if last is not None:
            levels += [last + (ppm - last) * Decimal(i) / Decimal(ramp) for i in range(1, ramp + 1)]
        levels += [ppm] * int(step["hold_samples"])
        last = ppm
    return levels


def values_for(scenario_name: str, uuid16: int, data_dir: Path) -> list[Decimal]:
    table = scenario_table()
    scenario = table["scenarios"].get(scenario_name)
    if scenario is None:
        raise TwinError("unknown scenario " + scenario_name)
    if scenario["kind"] == "co2_case":
        if uuid16 != int(scenario["select"], 16):
            raise TwinError("this scenario shapes another characteristic")
        return _co2_case(scenario, data_dir)
    if scenario["kind"] in {"recorded", "fault"}:
        return _recorded(table, uuid16, data_dir)
    raise TwinError("scenario does not apply to this protocol")


def _fault_frames(fault: dict[str, Any], uuid16: int, raws: list[int], seed: int) -> Iterator[Frame]:
    kind = fault["fault"]
    rng = random.Random(seed)
    rssi = fault.get("rssi_dbm")
    for index, raw in enumerate(raws):
        if kind == "disconnect" and index >= int(fault["after"]):
            raise LinkLost
        if kind == "low_signal" and (index + 1) % int(fault["drop_every"]) == 0:
            continue
        yield Frame(uuid16, _pack(uuid16, _faulted(kind, fault, uuid16, raws, index, raw, rng)), rssi)


def _faulted(
    kind: str, fault: dict[str, Any], uuid16: int, raws: list[int], i: int, raw: int, rng: random.Random
) -> int:
    start = int(fault.get("from_sample", 0))
    if kind == "spikes" and (i + 1) % int(fault["every"]) == 0:
        return raw + rng.choice((-1, 1)) * int(fault["magnitude_raw"])
    if kind == "drift":
        return raw + i * int(fault["raw_per_sample"])
    if i < start:
        return raw
    if kind == "constant_raw":
        return int(fault["raw"])
    if kind == "stuck":
        return raws[start - 1] if start > 0 else raw
    if kind in {"saturate", "not_known"}:
        code = ENCODINGS[uuid16][3] if kind == "saturate" else ENCODINGS[uuid16][2]
        if code is None:
            raise TwinError("this characteristic defines no saturation code")
        return code
    return raw


class EssTwin:
    """A simulated environmental sensing device for one characteristic and one scenario."""

    def __init__(self, uuid16: int, scenario_name: str, data_dir: Path, seed: int) -> None:
        table = scenario_table()
        self._table, self._uuid16, self._seed = table, uuid16, seed
        if uuid16 not in ENCODINGS:
            raise TwinError("unsupported characteristic")
        self._scenario = table["scenarios"].get(scenario_name)
        values = values_for(scenario_name, uuid16, data_dir)[:MAX_STEPS]
        limit = int(self._scenario.get("samples", MAX_STEPS))
        self._raws = [raw_of(uuid16, v) for v in values[:limit]]

    def identity(self) -> Identity:
        found = self._table["devices"]["ess"]
        return Identity(found["name"], found["serial"], bool(found["simulated"]))

    def frames(self) -> Iterator[Frame]:
        """Notifications in order. A disconnect scenario raises LinkLost after its frames."""
        if self._scenario["kind"] == "fault":
            yield from _fault_frames(self._scenario, self._uuid16, self._raws, self._seed)
            return
        for raw in self._raws:
            yield Frame(self._uuid16, _pack(self._uuid16, raw), None)

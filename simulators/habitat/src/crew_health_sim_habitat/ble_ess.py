"""Bluetooth Environmental Sensing (0x181A) twin: the GATT bytes a real cabin sensor sends.

The twin turns the simulated devices' register values into the characteristic values of the Bluetooth
GATT Specification Supplement (config/ble-ess.json names the sections) and serves them through a
BleakClient-shaped surface: connect, disconnect, read_gatt_char, start_notify, stop_notify, and the
disconnected_callback bleak calls on a lost link. ess_client_factory gives the (address,
disconnected_callback) factory shape E07's BLE transport takes (simulators/clinical/.../ble.py), so E23's
ble_ess adapter can run its real parser against the twin. Faults act on the link and on the bytes: a lost
link, a prohibited out-of-range code, a stuck value and a drifting value.

Bleak hands a notification callback the characteristic object; this twin hands it the characteristic's
UUID string, the field a parser reads from that object.
"""

import asyncio
import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from crew_health_sim_habitat.config import JsonObject, load_json, obj, objs, opt_obj, param, seconds, text
from crew_health_sim_habitat.devices import DeviceReading
from crew_health_sim_habitat.errors import ConfigError, SimulatorError
from crew_health_sim_habitat.runner import Tick

# A 16-bit UUID xxxx in full form, as bleak's normalize_uuid_16 writes it. Assigned Numbers section 3
# points to Bluetooth Core Specification Vol 3 Part B 2.5.1 for this base, which was not read here.
_BASE_UUID = "0000{:04x}-0000-1000-8000-00805f9b34fb"
# GSS data types: struct format (little endian, GSS section 2.4), the integer range of the type and the
# unsigned format of the same width, for special codes the GSS gives as bit patterns (0x8000).
_TYPES: Mapping[str, tuple[str, int, int, str]] = {
    "sint16": ("<h", -(2**15), 2**15 - 1, "<H"),
    "uint16": ("<H", 0, 2**16 - 1, "<H"),
    "uint32": ("<I", 0, 2**32 - 1, "<I"),
}
# SI prefixes between a device register's unit and the characteristic's unit.
_UNIT_FACTORS: Mapping[tuple[str, str], float] = {("kPa", "Pa"): 1000.0, ("Cel", "Cel"): 1.0, ("%", "%"): 1.0}
ESS_FAULTS = ("disconnect", "out_of_range", "stuck", "drift")
Notify = Callable[[str, bytearray], None]


class EssLinkError(SimulatorError):
    """The Bluetooth link is down (what bleak reports as a BleakError on a lost connection)."""


class EssEncodingError(SimulatorError):
    """A value has no representation in the characteristic's data type."""


def full_uuid(uuid16: int) -> str:
    return _BASE_UUID.format(uuid16)


@dataclass(frozen=True, slots=True)
class Characteristic:
    name: str
    uuid: str
    data_type: str
    decimal_exponent: int
    unit: str
    allowed_min: float | None
    allowed_max: float | None
    not_known_bits: int | None
    device_id: str
    output: str

    def raw(self, value: float) -> int:
        """Represented value to the integer the field carries: value = raw x 10^d (GSS, M = 1, b = 0)."""
        return round(value * 10.0 ** (-self.decimal_exponent))

    def pack(self, raw: int) -> bytes:
        fmt, low, high, _ = _TYPES[self.data_type]
        if not low <= raw <= high:
            raise EssEncodingError(f"{self.name}: {raw} does not fit {self.data_type}")
        return struct.pack(fmt, raw)

    def allowed(self, value: float) -> bool:
        low_ok = self.allowed_min is None or value >= self.allowed_min
        return low_ok and (self.allowed_max is None or value <= self.allowed_max)

    def encode(self, value: float | None) -> bytes:
        """The bytes a conforming sensor sends; an unknown or out-of-range value is 'value is not known'."""
        if value is not None and self.allowed(value):
            return self.pack(self.raw(value))
        if self.not_known_bits is None:
            raise EssEncodingError(f"{self.name} has no 'value is not known' code for {value!r}")
        return struct.pack(_TYPES[self.data_type][3], self.not_known_bits)


@dataclass(frozen=True, slots=True)
class EssFault:
    fault: str
    characteristic: str | None
    start_s: float
    duration_s: float
    value: float | None

    def active(self, t_s: float) -> bool:
        return self.start_s <= t_s < self.start_s + self.duration_s


@dataclass(frozen=True, slots=True)
class EssScenario:
    scenario_id: str
    base_scenario: str
    duration_s: float
    faults: tuple[EssFault, ...]


@dataclass(frozen=True, slots=True)
class EssConfig:
    twin_id: str
    service_uuid: str
    characteristics: tuple[Characteristic, ...]
    scenarios: Mapping[str, EssScenario]


def _uuid16(raw: JsonObject, key: str) -> int:
    """A 16-bit value written in hexadecimal (a UUID or a special code)."""
    label = text(raw, key)
    try:
        number = int(label, 16)
    except ValueError as exc:
        raise ConfigError(f"{key} {label!r} is not hexadecimal") from exc
    if not 0 <= number <= 0xFFFF:
        raise ConfigError(f"{key} {label!r} is not a 16-bit value")
    return number


def _optional_number(raw: JsonObject, key: str) -> float | None:
    item = raw.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int | float):
        raise ConfigError(f"{key} must be a number or null")
    return float(item)


def _characteristic(raw: JsonObject) -> Characteristic:
    data_type = text(raw, "data_type")
    exponent = raw.get("decimal_exponent")
    if data_type not in _TYPES or not isinstance(exponent, int) or isinstance(exponent, bool):
        raise ConfigError(f"characteristic {raw.get('name')!r}: unknown data type or exponent")
    source = obj(raw, "source")
    not_known = None if raw.get("not_known") is None else _uuid16(raw, "not_known")
    return Characteristic(
        name=text(raw, "name"),
        uuid=full_uuid(_uuid16(raw, "uuid16")),
        data_type=data_type,
        decimal_exponent=exponent,
        unit=text(raw, "unit"),
        allowed_min=_optional_number(raw, "allowed_min"),
        allowed_max=_optional_number(raw, "allowed_max"),
        not_known_bits=not_known,
        device_id=text(source, "device_id"),
        output=text(source, "output"),
    )


def _fault(raw: JsonObject, units: Mapping[str, str]) -> EssFault:
    fault = text(raw, "fault")
    target = raw.get("characteristic")
    if fault not in ESS_FAULTS or not (target is None or target in units):
        raise ConfigError(f"unknown ESS fault {fault!r} or characteristic {target!r}")
    value = None
    if fault in ("out_of_range", "drift"):
        if not isinstance(target, str):
            raise ConfigError(f"an ESS {fault} fault needs a characteristic")
        unit = units[target] + ("/h" if fault == "drift" else "")
        value = param(raw, "value", unit).value
    elif opt_obj(raw, "value") is not None:
        raise ConfigError(f"an ESS {fault} fault takes no value")
    name = target if isinstance(target, str) else None
    return EssFault(fault, name, seconds(raw, "start"), seconds(raw, "duration"), value)


def load_ess_config(path: Path) -> EssConfig:
    doc = load_json(path)
    characteristics = tuple(_characteristic(c) for c in objs(doc, "characteristics"))
    units = {c.name: c.unit for c in characteristics}
    if len(units) != len(characteristics):
        raise ConfigError("ESS characteristic names must be unique")
    scenarios: dict[str, EssScenario] = {}
    for raw in objs(doc, "fault_scenarios"):
        scenario = EssScenario(
            text(raw, "scenario_id"),
            text(raw, "base_scenario"),
            seconds(raw, "duration"),
            tuple(_fault(f, units) for f in objs(raw, "faults")),
        )
        scenarios[scenario.scenario_id] = scenario
    service = full_uuid(_uuid16(obj(doc, "service"), "uuid16"))
    return EssConfig(text(doc, "twin_id"), service, characteristics, scenarios)


def check_sources(config: EssConfig, units: Mapping[tuple[str, str], str]) -> None:
    """Every characteristic reads an existing device output whose unit converts to its own."""
    for char in config.characteristics:
        unit = units.get((char.device_id, char.output))
        if unit is None or (unit, char.unit) not in _UNIT_FACTORS:
            where = f"{char.device_id}.{char.output}"
            raise ConfigError(f"ESS {char.name}: {where} is missing or not in a usable unit")


class EssTwin:
    """A BleakClient-shaped ESS peripheral fed by the simulated devices through advance() or play()."""

    simulated = True

    def __init__(
        self,
        config: EssConfig,
        faults: Sequence[EssFault] = (),
        disconnected_callback: Callable[[EssTwin], None] | None = None,
    ) -> None:
        self.address = config.twin_id
        self._disconnected_callback = disconnected_callback
        self.service_uuids = (config.service_uuid,)
        self._chars = {c.uuid: c for c in config.characteristics}
        self._faults = tuple(faults)
        self._connected = False
        self._link_down = False
        self._values: dict[str, bytes] = {}
        self._subscribers: dict[str, Notify] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        if self._link_down:
            raise EssLinkError(f"{self.address} is out of reach")
        self._connected = True
        return True

    async def disconnect(self) -> bool:
        self._connected = False
        self._subscribers.clear()
        return True

    def _char(self, char_specifier: str) -> Characteristic:
        char = self._chars.get(char_specifier.lower())
        if char is None:
            raise EssLinkError(f"{self.address} has no characteristic {char_specifier}")
        return char

    async def read_gatt_char(self, char_specifier: str) -> bytearray:
        char = self._char(char_specifier)
        if not self._connected:
            raise EssLinkError(f"{self.address} is not connected")
        if char.uuid in self._values:
            return bytearray(self._values[char.uuid])
        return bytearray(char.encode(None))

    async def start_notify(self, char_specifier: str, callback: Notify) -> None:
        char = self._char(char_specifier)
        if not self._connected:
            raise EssLinkError(f"{self.address} is not connected")
        self._subscribers[char.uuid] = callback

    async def stop_notify(self, char_specifier: str) -> None:
        self._subscribers.pop(self._char(char_specifier).uuid, None)

    def _active(self, t_s: float, fault: str, char: Characteristic | None) -> EssFault | None:
        for item in self._faults:
            matches = char is None or item.characteristic == char.name
            if item.fault == fault and item.active(t_s) and matches:
                return item
        return None

    def _bytes(self, char: Characteristic, register: float, t_s: float) -> bytes:
        if self._active(t_s, "stuck", char) is not None and char.uuid in self._values:
            return self._values[char.uuid]
        out_of_range = self._active(t_s, "out_of_range", char)
        if out_of_range is not None and out_of_range.value is not None:
            return char.pack(char.raw(out_of_range.value))
        drift = self._active(t_s, "drift", char)
        if drift is not None and drift.value is not None:
            register += drift.value * (t_s - drift.start_s) / 3600.0
        return char.encode(register)

    def advance(self, t_s: float, readings: Sequence[DeviceReading]) -> None:
        """Take the devices' new register values at t; notify subscribers while the link is up."""
        self._link_down = self._active(t_s, "disconnect", None) is not None
        if self._link_down and self._connected:
            self._connected = False
            self._subscribers.clear()
            if self._disconnected_callback is not None:
                self._disconnected_callback(self)
        for reading in readings:
            for char in self._chars.values():
                if (reading.device_id, reading.output) != (char.device_id, char.output):
                    continue
                factor = _UNIT_FACTORS[(reading.quantity.unit, char.unit)]
                data = self._bytes(char, reading.quantity.value * factor, t_s)
                self._values[char.uuid] = data
                callback = self._subscribers.get(char.uuid)
                if callback is not None and self._connected:
                    callback(char.uuid, bytearray(data))

    async def play(self, ticks: Iterable[Tick], pace_s: float = 0.0) -> None:
        """Advance through a bounded run of simulated ticks, yielding to the event loop between them."""
        for tick in ticks:
            self.advance(tick.t_s, tick.readings)
            await asyncio.sleep(pace_s)


def ess_client_factory(
    config: EssConfig, faults: Sequence[EssFault] = ()
) -> Callable[[str, Callable[[EssTwin], None] | None], EssTwin]:
    """The (address, disconnected_callback) factory a BLE transport takes in place of BleakClient."""

    def factory(address: str, disconnected_callback: Callable[[EssTwin], None] | None = None) -> EssTwin:
        if address != config.twin_id:
            raise EssLinkError(f"no peripheral at {address!r}; the twin is {config.twin_id!r}")
        return EssTwin(config, faults, disconnected_callback)

    return factory

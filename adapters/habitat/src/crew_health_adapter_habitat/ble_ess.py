"""Bluetooth Environmental Sensing (service 0x181A) characteristic parsing and the link ports (E23 5.2).

NASA publishes no Bluetooth profile, so the characteristic table below is protocol data from two Bluetooth SIG
documents (non-NASA sources). Both copies were read and match the SHA-256 the simulator's provenance records:
- GSS: Bluetooth SIG, GATT Specification Supplement, version date 2026-09-09, SHA-256
  34ad6b5f2e48c29759c2ef17f01502cb60080aff5d213712a6f581c56c9150b8. Fields are little endian (Section 2.4,
  page 18); represented value = raw x 10^d with M = 1 and b = 0.
- AN: Bluetooth SIG, Assigned Numbers, version date 2025-03-13, SHA-256
  238af46196b6137978f9c55ec82ca99d288b06b7950548bd2e959d3b2ff29a00. Service 0x181A is Section 3.4.2, page 69;
  characteristic UUIDs are Section 3.8.2 (pages 98 and 105).
A value the device marks unknown, saturated or outside the allowed range is rejected with a code, never turned
into a number. LIVE stays refused until sourced plausibility criteria exist (R-E23-2). This table is written from
those documents and never read from the simulator's configuration, so the parser and the twin stay independent.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from crew_health_adapter_habitat.errors import LiveRefused, ParseRejected, SourceUnavailable
from crew_health_adapter_habitat.ports import (
    Clock,
    Criteria,
    Origin,
    Quantity,
    Settings,
    SourceItem,
    SourceRow,
)

ESS_SERVICE_UUID16 = 0x181A


_SIZES = {"uint16": 2, "sint16": 2, "uint32": 4}


@dataclass(frozen=True, slots=True)
class CharacteristicSpec:
    """One GATT characteristic: its type, resolution, special codes and allowed raw range, with its source."""

    uuid16: int
    name: str
    fmt: Literal["uint16", "sint16", "uint32"]
    resolution: Decimal
    unit: str
    unknown_raw: int | None
    saturated_raw: int | None
    min_raw: int | None
    max_raw: int | None
    source: str

    @property
    def size(self) -> int:
        return _SIZES[self.fmt]


# Unit spellings are the hub's wire spellings (config/unit-mapping.json), not UCUM.
ESS_CHARACTERISTICS: Mapping[int, CharacteristicSpec] = {
    0x2A6E: CharacteristicSpec(
        0x2A6E, "Temperature", "sint16", Decimal("0.01"), "Cel", 0x8000, None, -27315, 32767,
        "GSS Section 3.235, Table 3.361, pages 184-185: sint16, d = -2, 0.01 degC, allowed -273.15 to 327.67, "
        "0x8000 is 'value is not known', all other values prohibited; UUID AN Section 3.8.2, page 98",
    ),
    0x2A6F: CharacteristicSpec(
        0x2A6F, "Humidity", "uint16", Decimal("0.01"), "%", 0xFFFF, None, 0, 10000,
        "GSS Section 3.134, Table 3.226, page 123: uint16, d = -2, 0.01 %, allowed 0.00 to 100.00, 0xFFFF is "
        "'value is not known', all other values prohibited; UUID AN Section 3.8.2, page 98",
    ),
    0x2A6D: CharacteristicSpec(
        0x2A6D, "Pressure", "uint32", Decimal("0.1"), "Pa", None, None, None, None,
        "GSS Section 3.195, Table 3.303, pages 156-157: uint32, d = -1, 0.1 Pa, no stated range and no "
        "'value is not known' code; UUID AN Section 3.8.2, page 98",
    ),
    0x2B8C: CharacteristicSpec(
        0x2B8C, "CO2 Concentration", "uint16", Decimal(1), "ppm", 0xFFFF, 0xFFFE, 0, 65533,
        "GSS Section 3.54, Table 3.94, page 63: uint16, d = 0, 1 ppm, allowed 0 to 65533, 0xFFFE is 'value is "
        "65534 or greater', 0xFFFF is 'value is not known'; UUID AN Section 3.8.2, page 105",
    ),
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Who the device says it is. A simulated device declares it, so the hub labels its readings SIM."""

    name: str
    serial: str
    simulated: bool


@dataclass(frozen=True, slots=True)
class Notification:
    uuid16: int
    payload: bytes
    rssi_dbm: int | None = None


class BleLink(Protocol):
    def identity(self) -> DeviceIdentity: ...

    def notifications(self) -> Iterator[Notification]:
        """Yield notifications until the device disconnects, then raise SourceUnavailable."""
        ...

    def close(self) -> None: ...


def decode(spec: CharacteristicSpec, payload: bytes) -> Decimal:
    """The value of one notification in the characteristic's unit, or ParseRejected with a safe code."""
    if len(payload) != spec.size:
        raise ParseRejected("BAD_LENGTH")
    raw = int.from_bytes(payload, "little", signed=spec.fmt == "sint16")
    unsigned = int.from_bytes(payload, "little")
    if spec.unknown_raw is not None and unsigned == spec.unknown_raw:
        raise ParseRejected("NOT_KNOWN")
    if spec.saturated_raw is not None and unsigned == spec.saturated_raw:
        raise ParseRejected("SATURATED")
    if (spec.min_raw is not None and raw < spec.min_raw) or (spec.max_raw is not None and raw > spec.max_raw):
        raise ParseRejected("OUT_OF_RANGE")
    return Decimal(raw) * spec.resolution


def origin_of(identity: DeviceIdentity, criteria: Criteria | None) -> Origin:
    """SIM when the device declares itself simulated. LIVE needs sourced criteria first (R-E23-2)."""
    if identity.simulated:
        return "SIM"
    if criteria is None:
        raise LiveRefused("no sourced plausibility criteria for this sensor type")
    return "LIVE"


class EssSource:
    """Turns the link's notifications for one characteristic into source items. A live stream has no resume."""

    def __init__(self, link: BleLink, channel_id: str, uuid16: int, clock: Clock) -> None:
        spec = ESS_CHARACTERISTICS.get(uuid16)
        if spec is None:
            raise ParseRejected("UNKNOWN_CHARACTERISTIC")
        self.link, self.channel_id, self.spec, self.clock = link, channel_id, spec, clock

    def items(self, after: int) -> Iterator[SourceItem]:
        position = max(after, 0)
        for note in self.link.notifications():
            if note.uuid16 != self.spec.uuid16:
                continue
            position += 1
            yield self._item(position, note, self.clock.now())

    def _item(self, position: int, note: Notification, at: datetime) -> SourceItem:
        try:
            value = decode(self.spec, note.payload)
        except ParseRejected as rejected:
            return SourceItem(position, None, rejected.code)
        return SourceItem(position, SourceRow(self.channel_id, Quantity(value, self.spec.unit), at))


class BleakLink:
    """The real device through `bleak`. Hardware only: it needs an adapter and a device, so it is exercised by the
    `hardware` test and by nothing in the default run. It never declares itself simulated."""

    def __init__(self, address: str, uuids: tuple[int, ...], settings: Settings) -> None:
        self.address, self.uuids, self.settings = address, uuids, settings
        self.inbox: queue.Queue[Notification | None] = queue.Queue(maxsize=settings.batch_max_readings)
        self.overflow = 0
        self.thread: threading.Thread | None = None

    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(name="BLE ESS " + self.address, serial=self.address, simulated=False)

    def notifications(self) -> Iterator[Notification]:
        self._start()
        while True:
            try:
                note = self.inbox.get(timeout=self.settings.http_timeout_s)
            except queue.Empty as error:
                raise SourceUnavailable("no notification within the timeout") from error
            if note is None:
                raise SourceUnavailable("device disconnected")
            yield note

    def close(self) -> None:
        if self.thread is not None:
            self.thread.join(timeout=self.settings.http_timeout_s)

    def _start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="ble-ess", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        import asyncio

        asyncio.run(self._session())

    async def _session(self) -> None:
        import asyncio

        from bleak import BleakClient

        def deliver(uuid16: int) -> object:
            def callback(_: object, data: bytearray) -> None:
                try:
                    self.inbox.put_nowait(Notification(uuid16, bytes(data)))
                except queue.Full:
                    self.overflow += 1

            return callback

        try:
            async with BleakClient(self.address, timeout=self.settings.http_timeout_s) as client:
                for uuid16 in self.uuids:
                    await client.start_notify(_long_uuid(uuid16), deliver(uuid16))  # type: ignore[arg-type]
                while client.is_connected:
                    await asyncio.sleep(self.settings.batch_interval_s)
        finally:
            try:
                self.inbox.put(None, timeout=self.settings.http_timeout_s)
            except queue.Full:
                self.overflow += 1


def _long_uuid(uuid16: int) -> str:
    return f"0000{uuid16:04x}-0000-1000-8000-00805f9b34fb"

"""CCSDS space packet twin: an XTCE document and the packets it describes, fed by the simulated devices.

A read only telemetry feed carries one float32 parameter per packet in a CCSDS space packet: a six octet
primary header (version 0, telemetry, no secondary header, unsegmented, an 11 bit APID, a 14 bit sequence
count, a length field equal to the data octets minus one) and big endian data. config/ccsds-packets.json
holds the streams and names its sources. E23's xtce adapter parses the document and the packets.

The twin never reads a command and never writes to a vehicle. A device that is warming up has not measured
yet, so no packet is sent for it; a gap stays a gap. A value that does not fit a float32 is refused.
"""

import math
import struct
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from crew_health_sim_habitat.config import load_json, obj, objs, text
from crew_health_sim_habitat.devices import STATUS_WARMING_UP, DeviceReading
from crew_health_sim_habitat.errors import ConfigError, NonFiniteValueError, SimulatorError
from crew_health_sim_habitat.runner import Tick

APID_MAX = 0x7FF
SEQUENCE_COUNT_MODULO = 0x4000
FLOAT32_MAX = 3.4028234663852886e38
_XTCE_NAMESPACE = "http://www.omg.org/spec/XTCE/20180204"
_UNSEGMENTED_TELEMETRY = 0xC000  # sequence flags 11, as the high bits of the second header word


class PacketError(SimulatorError):
    """A packet cannot be built: a value outside float32, or a parameter that is not a stream."""


@dataclass(frozen=True, slots=True)
class PacketStream:
    name: str
    apid: int
    unit: str
    device_id: str
    output: str


@dataclass(frozen=True, slots=True)
class PacketConfig:
    twin_id: str
    space_system: str
    device_name: str
    device_serial: str
    streams: tuple[PacketStream, ...]

    def stream(self, name: str) -> PacketStream:
        for stream in self.streams:
            if stream.name == name:
                return stream
        raise PacketError(f"no packet stream {name!r}; known: {sorted(s.name for s in self.streams)}")


def space_packet(apid: int, data: bytes, count: int) -> bytes:
    """One telemetry space packet. The length field is the data octets minus one, so data is never empty."""
    if not 0 <= apid <= APID_MAX:
        raise PacketError(f"APID {apid} is not an 11 bit number")
    if not data:
        raise PacketError("a space packet carries at least one data octet")
    sequence = _UNSEGMENTED_TELEMETRY | (count % SEQUENCE_COUNT_MODULO)
    return struct.pack(">HHH", apid, sequence, len(data) - 1) + data


def _stream(raw: Mapping[str, object]) -> PacketStream:
    apid = raw.get("apid")
    if isinstance(apid, bool) or not isinstance(apid, int) or not 0 <= apid <= APID_MAX:
        raise ConfigError(f"stream {raw.get('name')!r}: apid must be an integer from 0 to {APID_MAX}")
    source = obj(raw, "source")
    return PacketStream(
        text(raw, "name"), apid, text(raw, "unit"), text(source, "device_id"), text(source, "output")
    )


def load_packet_config(path: Path) -> PacketConfig:
    doc = load_json(path)
    streams = tuple(_stream(raw) for raw in objs(doc, "streams"))
    if len({s.name for s in streams}) != len(streams) or len({s.apid for s in streams}) != len(streams):
        raise ConfigError("packet stream names and APIDs must be unique")
    return PacketConfig(
        text(doc, "twin_id"),
        text(doc, "space_system"),
        text(doc, "device_name"),
        text(doc, "device_serial"),
        streams,
    )


def check_packet_sources(config: PacketConfig, units: Mapping[tuple[str, str], str]) -> None:
    """Every stream reads an existing device output in the unit the stream declares (no conversion)."""
    for stream in config.streams:
        unit = units.get((stream.device_id, stream.output))
        if unit != stream.unit:
            where = f"{stream.device_id}.{stream.output}"
            raise ConfigError(f"stream {stream.name}: {where} is missing or not in {stream.unit!r}")


def xtce_document(config: PacketConfig, names: Iterable[str] | None = None) -> bytes:
    """The XTCE document of the chosen streams (all by default): one parameter and one container each."""
    chosen = config.streams if names is None else tuple(config.stream(n) for n in names)
    types = "".join(
        f"<FloatParameterType name={quoteattr(s.name + '_type')}>"
        f"<UnitSet><Unit>{escape(s.unit)}</Unit></UnitSet>"
        '<FloatDataEncoding sizeInBits="32" byteOrder="mostSignificantByteFirst"/></FloatParameterType>'
        for s in chosen
    )
    params = "".join(
        f"<Parameter name={quoteattr(s.name)} parameterTypeRef={quoteattr(s.name + '_type')}/>"
        for s in chosen
    )
    containers = "".join(
        f"<SequenceContainer name={quoteattr(s.name + '_packet')}><EntryList>"
        f"<ParameterRefEntry parameterRef={quoteattr(s.name)}/></EntryList>"
        '<BaseContainer containerRef="CCSDSPacket"><RestrictionCriteria>'
        f'<Comparison parameterRef="APID" value="{s.apid}"/></RestrictionCriteria></BaseContainer>'
        "</SequenceContainer>"
        for s in chosen
    )
    body = (
        f"<TelemetryMetaData><ParameterTypeSet>{types}</ParameterTypeSet><ParameterSet>{params}</ParameterSet>"
        f"<ContainerSet>{containers}</ContainerSet></TelemetryMetaData>"
    )
    head = f'<SpaceSystem xmlns="{_XTCE_NAMESPACE}" name={quoteattr(config.space_system)}>'
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + body + "</SpaceSystem>").encode("utf-8")


def float32_bytes(value: float) -> bytes:
    if not math.isfinite(value):
        raise NonFiniteValueError("a packet never carries NaN or infinity")
    if abs(value) > FLOAT32_MAX:
        raise PacketError(f"{value!r} does not fit a float32")
    return struct.pack(">f", value)


class PacketTwin:
    """A CCSDS packet source for one stream, fed by the simulated devices' ticks."""

    simulated = True

    def __init__(self, config: PacketConfig, stream_name: str) -> None:
        self.config = config
        self.stream = config.stream(stream_name)
        self._count = 0

    @property
    def address(self) -> str:
        return self.config.twin_id

    def xtce_document(self) -> bytes:
        return xtce_document(self.config, (self.stream.name,))

    def packet_for(self, reading: DeviceReading) -> bytes | None:
        """The packet of one reading of this stream's device output, or None when it is not this stream or
        the device is still warming up."""
        if (reading.device_id, reading.output) != (self.stream.device_id, self.stream.output):
            return None
        if reading.status == STATUS_WARMING_UP:
            return None
        if reading.quantity.unit != self.stream.unit:
            raise ConfigError(f"stream {self.stream.name}: the device reports {reading.quantity.unit!r}")
        packet = space_packet(self.stream.apid, float32_bytes(reading.quantity.value), self._count)
        self._count += 1
        return packet

    def packets(self, ticks: Iterable[Tick]) -> Iterator[bytes]:
        """Packets for a bounded run of simulated ticks, in tick order."""
        for tick in ticks:
            for reading in tick.readings:
                packet = self.packet_for(reading)
                if packet is not None:
                    yield packet

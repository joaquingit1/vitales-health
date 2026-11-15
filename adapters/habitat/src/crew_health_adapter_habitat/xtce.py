"""Read-only CCSDS space packets described by an XTCE document (E23 5.2, D3.6).

A subset of XTCE is read: integer and float parameter types (big-endian, with a polynomial default calibrator and
a unit), a parameter set, and sequence containers selected by an APID restriction. The adapter only reads; it
never builds a command, writes to a vehicle, or claims a mission interface. Parser support only this weekend.
A packet or value that cannot be decoded is rejected with a code; nothing is substituted.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crew_health_adapter_habitat.errors import FeedInvalid, ParseRejected
from crew_health_adapter_habitat.ports import Clock, Quantity, SourceItem, SourceRow, parse_decimal

MAX_XTCE_BYTES = 1024 * 1024  # implementation bound on one XTCE document
PRIMARY_HEADER_BYTES = 6


@dataclass(frozen=True, slots=True)
class ParameterType:
    kind: str  # unsigned, signed or float
    size_bits: int
    unit: str
    terms: tuple[tuple[Decimal, int], ...]  # (coefficient, exponent) of the default calibrator


@dataclass(frozen=True, slots=True)
class Container:
    name: str
    apid: int
    entries: tuple[tuple[str, ParameterType], ...]  # parameter name, type, in packet order


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    quantity: Quantity


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _tag(child) == name]


def _find(element: ET.Element, name: str) -> ET.Element | None:
    for found in element.iter():
        if _tag(found) == name:
            return found
    return None


def _ptype(element: ET.Element) -> ParameterType:
    encoding = _find(element, "IntegerDataEncoding")
    if encoding is None:
        encoding = _find(element, "FloatDataEncoding")
    if encoding is None or encoding.get("sizeInBits") is None:
        raise FeedInvalid("XTCE parameter type has no encoding")
    if encoding.get("byteOrder", "mostSignificantByteFirst") != "mostSignificantByteFirst":
        raise FeedInvalid("only big-endian XTCE encodings are read")
    size = int(encoding.get("sizeInBits", "0"))
    kind = "float" if _tag(encoding) == "FloatDataEncoding" else encoding.get("encoding", "unsigned")
    if kind not in {"unsigned", "signed", "float"} or size <= 0 or size % 8 or size > 64:
        raise FeedInvalid("unsupported XTCE encoding")
    unit_element = _find(element, "Unit")
    unit = "" if unit_element is None or unit_element.text is None else unit_element.text.strip()
    if not unit:
        raise FeedInvalid("every XTCE parameter type declares its unit")
    terms = tuple(
        (Decimal(t.get("coefficient", "0")), int(t.get("exponent", "0")))
        for t in element.iter()
        if _tag(t) == "Term"
    )
    return ParameterType(kind, size, unit, terms)


def load_xtce(document: bytes) -> tuple[Container, ...]:
    """Parse an XTCE document. External entities and DTDs are refused before parsing."""
    if len(document) > MAX_XTCE_BYTES or b"<!DOCTYPE" in document or b"<!ENTITY" in document:
        raise FeedInvalid("XTCE document refused")
    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        raise FeedInvalid("XTCE document is not well formed") from error
    types = {
        e.get("name", ""): _ptype(e)
        for e in root.iter()
        if _tag(e) in {"IntegerParameterType", "FloatParameterType"}
    }
    params = {e.get("name", ""): e.get("parameterTypeRef", "") for e in root.iter() if _tag(e) == "Parameter"}
    containers = tuple(_container(c, params, types) for c in root.iter() if _tag(c) == "SequenceContainer")
    if not containers:
        raise FeedInvalid("XTCE document has no sequence container")
    return containers


def _container(element: ET.Element, params: dict[str, str], types: dict[str, ParameterType]) -> Container:
    apid = None
    for comparison in element.iter():
        if _tag(comparison) == "Comparison" and comparison.get("parameterRef", "").endswith("APID"):
            apid = int(comparison.get("value", "-1"))
    if apid is None or not 0 <= apid <= 0x7FF:
        raise FeedInvalid("container has no valid APID restriction")
    entry_list = _find(element, "EntryList")
    names = (
        []
        if entry_list is None
        else [e.get("parameterRef", "") for e in entry_list if _tag(e) == "ParameterRefEntry"]
    )
    try:
        entries = tuple((name, types[params[name]]) for name in names)
    except KeyError as error:
        raise FeedInvalid("container names an undeclared parameter or type") from error
    return Container(element.get("name", ""), apid, entries)


def parse_packet(containers: tuple[Container, ...], packet: bytes) -> list[Parameter]:
    """Decode one space packet. Raises ParseRejected with a safe code; never returns a partial packet."""
    if len(packet) <= PRIMARY_HEADER_BYTES:
        raise ParseRejected("SHORT_PACKET")
    word0, _, length = struct.unpack(">HHH", packet[:PRIMARY_HEADER_BYTES])
    if word0 >> 13 != 0 or (word0 >> 12) & 1:
        raise ParseRejected("NOT_TELEMETRY")
    data = packet[PRIMARY_HEADER_BYTES:]
    if length + 1 != len(data):
        raise ParseRejected("BAD_LENGTH")
    chosen = [c for c in containers if c.apid == word0 & 0x7FF]
    if not chosen:
        raise ParseRejected("UNKNOWN_APID")
    return _decode(chosen[0], data)


def _decode(container: Container, data: bytes) -> list[Parameter]:
    found: list[Parameter] = []
    cursor = 0
    for name, ptype in container.entries:
        size = ptype.size_bits // 8
        chunk = data[cursor : cursor + size]
        if len(chunk) != size:
            raise ParseRejected("BAD_LENGTH")
        cursor += size
        found.append(Parameter(name, Quantity(_value(ptype, chunk), ptype.unit)))
    if cursor != len(data):
        raise ParseRejected("BAD_LENGTH")
    return found


def _value(ptype: ParameterType, chunk: bytes) -> Decimal:
    if ptype.kind == "float":
        if len(chunk) not in {4, 8}:
            raise ParseRejected("BAD_ENCODING")
        raw: Decimal | None = parse_decimal(repr(struct.unpack(">f" if len(chunk) == 4 else ">d", chunk)[0]))
        if raw is None:
            raise ParseRejected("NON_FINITE")
    else:
        raw = Decimal(int.from_bytes(chunk, "big", signed=ptype.kind == "signed"))
    return _calibrate(ptype, raw)


def _calibrate(ptype: ParameterType, raw: Decimal) -> Decimal:
    if not ptype.terms:
        return raw
    return sum((c * raw**e for c, e in ptype.terms), Decimal(0))


class XtceSource:
    """Source of one named parameter from a stream of packets. A live stream has no resume."""

    def __init__(
        self,
        containers: tuple[Container, ...],
        packets: Iterator[bytes],
        channel_id: str,
        parameter: str,
        clock: Clock,
    ) -> None:
        self.containers, self.packets, self.channel_id = containers, packets, channel_id
        self.parameter, self.clock = parameter, clock

    def items(self, after: int) -> Iterator[SourceItem]:
        position = max(after, 0)
        for packet in self.packets:
            position += 1
            yield self._item(position, packet, self.clock.now())

    def _item(self, position: int, packet: bytes, at: datetime) -> SourceItem:
        try:
            decoded = parse_packet(self.containers, packet)
        except ParseRejected as rejected:
            return SourceItem(position, None, rejected.code)
        for parameter in decoded:
            if parameter.name == self.parameter:
                return SourceItem(position, SourceRow(self.channel_id, parameter.quantity, at))
        return SourceItem(position, None, "PARAMETER_ABSENT")

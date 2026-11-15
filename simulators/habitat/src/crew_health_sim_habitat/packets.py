"""The CCSDS packet twin: an XTCE document and the space packets it describes, built from recorded NASA values.

It emits what a read-only telemetry feed would carry. It never reads a command and never writes to a vehicle.
Packets are big-endian CCSDS space packets (CCSDS 133.0-B) with a 6 byte primary header and float32 parameters.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from crew_health_sim_habitat.data import MAX_STEPS, TwinError, column, read_rows, scenario_table
from crew_health_sim_habitat.ess import Identity, LinkLost

_SPACE_SYSTEM = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<SpaceSystem xmlns="http://www.omg.org/spec/XTCE/20180204" name="HabitatTwin">'
    "<TelemetryMetaData>"
    "<ParameterTypeSet>"
    '<FloatParameterType name="Float32"><UnitSet><Unit>{unit}</Unit></UnitSet>'
    '<FloatDataEncoding sizeInBits="32" byteOrder="mostSignificantByteFirst"/></FloatParameterType>'
    "</ParameterTypeSet>"
    '<ParameterSet><Parameter name="{name}" parameterTypeRef="Float32"/></ParameterSet>'
    "<ContainerSet>"
    '<SequenceContainer name="{name}_packet"><EntryList><ParameterRefEntry parameterRef="{name}"/></EntryList>'
    '<BaseContainer containerRef="CCSDSPacket"><RestrictionCriteria>'
    '<Comparison parameterRef="APID" value="{apid}"/></RestrictionCriteria></BaseContainer>'
    "</SequenceContainer>"
    "</ContainerSet></TelemetryMetaData></SpaceSystem>"
)


def space_packet(apid: int, data: bytes, count: int) -> bytes:
    """Version 0, telemetry, no secondary header, unsegmented; the length field is data octets minus one."""
    return struct.pack(">HHH", apid & 0x7FF, 0xC000 | (count & 0x3FFF), len(data) - 1) + data


class PacketTwin:
    """A simulated packet source for one named parameter and one scenario."""

    def __init__(self, parameter: str, scenario_name: str, data_dir: Path) -> None:
        table = scenario_table()
        self._table = table
        scenario = table["scenarios"].get(scenario_name)
        if scenario is None:
            raise TwinError("unknown scenario " + scenario_name)
        self._scenario = scenario
        source = table["packet_sources"].get(parameter)
        if scenario["kind"] == "spe_case":
            source = self._spe_source(scenario, data_dir)
            parameter = "dose_rate"
        if source is None:
            raise TwinError("no recorded source for this parameter")
        self._parameter, self._source = parameter, source
        self._values = [float(v) for v in self._load(scenario, source, data_dir)[:MAX_STEPS]]

    def _spe_source(self, scenario: dict[str, Any], data_dir: Path) -> dict[str, Any]:
        return {
            "file": scenario["data_file"],
            "time_column": "timestamp",
            "value_column": "absorbed_dose_rate",
            "unit": scenario["unit"],
            "apid": scenario["apid"],
            "filter": {"column": "instrument_id", "equals": scenario["instrument"]},
        }

    def _load(self, scenario: dict[str, Any], source: dict[str, Any], data_dir: Path) -> list[Any]:
        rows = read_rows(data_dir / source["file"])
        if scenario["kind"] == "spe_case":
            low, high = scenario["from"], scenario["to"]
            rows = [r for r in rows if low <= r["timestamp"] <= high]
        return column(rows, source["value_column"], source.get("filter")) * int(source.get("repeat", 1))

    def identity(self) -> Identity:
        found = self._table["devices"]["packets"]
        return Identity(found["name"], found["serial"], bool(found["simulated"]))

    def xtce_document(self) -> bytes:
        text = _SPACE_SYSTEM.format(
            unit=self._source["unit"], name=self._parameter, apid=self._source["apid"]
        )
        return text.encode("utf-8")

    def packets(self) -> Iterator[bytes]:
        kind, scenario = self._scenario["kind"], self._scenario
        held: float | None = None
        for index, value in enumerate(self._values):
            if kind == "fault" and scenario["fault"] == "disconnect" and index >= int(scenario["after"]):
                raise LinkLost
            if kind == "fault" and scenario["fault"] == "stuck":
                if index >= int(scenario["from_sample"]):
                    held = self._values[int(scenario["from_sample"]) - 1] if held is None else held
                    value = held
            yield space_packet(int(self._source["apid"]), struct.pack(">f", value), index)

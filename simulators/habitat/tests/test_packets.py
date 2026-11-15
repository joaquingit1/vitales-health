"""The CCSDS packet twin: bytes worked by hand from the Space Packet Protocol header, and the XTCE document.

Expected bytes are written out here, never taken from the twin or its config. A packet is a six octet primary
header (version 0, telemetry, no secondary header, unsegmented, APID, sequence count, data length minus one)
and a big endian float32. 1.5 = 0x3FC00000; APID 513 = 0x0201; flags 11 and count 0 give 0xC000.
"""

import json
import shutil
import struct
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.devices import STATUS_OK, STATUS_WARMING_UP, DeviceReading
from crew_health_sim_habitat.errors import ConfigError, NonFiniteValueError
from crew_health_sim_habitat.packets import (
    PacketError,
    PacketTwin,
    check_packet_sources,
    float32_bytes,
    load_packet_config,
    space_packet,
    xtce_document,
)
from crew_health_sim_habitat.runner import build_devices, simulate
from crew_health_sim_habitat.units import Quantity

from conftest import REPO_ROOT

CONFIG = REPO_ROOT / "simulators" / "habitat" / "config" / "ccsds-packets.json"
XTCE = "{http://www.omg.org/spec/XTCE/20180204}"


def test_a_packet_is_the_hand_worked_header_and_a_big_endian_float() -> None:
    packet = space_packet(513, struct.pack(">f", 1.5), 0)
    assert packet == b"\x02\x01\xc0\x00\x00\x03" + b"\x3f\xc0\x00\x00"
    assert space_packet(513, b"\x00\x00\x00\x00", 5)[2:4] == b"\xc0\x05"
    assert (
        space_packet(513, b"\x00\x00\x00\x00", 0x4000 + 7)[2:4] == b"\xc0\x07"
    )  # the count wraps at 14 bits
    assert space_packet(0x7FF, b"\x01", 0) == b"\x07\xff\xc0\x00\x00\x00\x01"


def test_a_packet_that_cannot_be_formed_is_refused() -> None:
    with pytest.raises(PacketError):
        space_packet(0x800, b"\x00", 0)
    with pytest.raises(PacketError):
        space_packet(1, b"", 0)
    with pytest.raises(PacketError):
        float32_bytes(3.5e38)
    with pytest.raises(NonFiniteValueError):
        float32_bytes(float("nan"))
    with pytest.raises(NonFiniteValueError):
        float32_bytes(float("inf"))


@settings(max_examples=200, deadline=None)
@given(value=st.floats(min_value=-1.0e30, max_value=1.0e30, allow_nan=False), count=st.integers(0, 100000))
def test_header_fields_round_trip(value: float, count: int) -> None:
    packet = space_packet(514, float32_bytes(value), count)
    word0, word1, length = struct.unpack(">HHH", packet[:6])
    assert word0 >> 11 == 0  # version 0, telemetry, no secondary header
    assert word0 & 0x7FF == 514
    assert word1 >> 14 == 3
    assert word1 & 0x3FFF == count % 0x4000
    assert length + 1 == len(packet) - 6 == 4
    assert struct.unpack(">f", packet[6:])[0] == pytest.approx(value, rel=1e-6, abs=1e-30)


def test_the_document_declares_each_stream_with_its_unit_and_apid(loaded: Loaded) -> None:
    config = load_packet_config(CONFIG)
    root = ET.fromstring(xtce_document(config))
    containers = {c.get("name"): c for c in root.iter(XTCE + "SequenceContainer")}
    assert set(containers) == {s.name + "_packet" for s in config.streams}
    for stream in config.streams:
        comparison = next(containers[stream.name + "_packet"].iter(XTCE + "Comparison"))
        assert comparison.get("value") == str(stream.apid)
    units = {
        t.get("name"): t.findtext(f"{XTCE}UnitSet/{XTCE}Unit") for t in root.iter(XTCE + "FloatParameterType")
    }
    assert units == {s.name + "_type": s.unit for s in config.streams}
    one = ET.fromstring(xtce_document(config, ("dose_rate",)))
    assert [c.get("name") for c in one.iter(XTCE + "SequenceContainer")] == ["dose_rate_packet"]


def test_the_configured_sources_exist_in_the_simulated_devices(loaded: Loaded) -> None:
    config = load_packet_config(CONFIG)
    units = {
        (d.device_id, o.name): o.unit for d in loaded.wiring.devices for o in loaded.kinds[d.kind].outputs
    }
    check_packet_sources(config, units)
    wrong = replace(config, streams=(replace(config.streams[0], unit="mmHg"),))
    with pytest.raises(ConfigError):
        check_packet_sources(wrong, units)


def test_the_twin_streams_the_device_values_as_packets(loaded: Loaded) -> None:
    config = load_packet_config(CONFIG)
    scenario = replace(loaded.scenario("station_nominal"), duration_s=300.0)
    twin = PacketTwin(config, "co2_ppm")
    ticks = list(simulate(scenario, build_devices(scenario, loaded.wiring, loaded.kinds)))
    packets = list(twin.packets(ticks))
    values = [
        r.quantity.value
        for t in ticks
        for r in t.readings
        if (r.device_id, r.output) == ("sim_ndir_co2_cabin", "co2") and r.status != STATUS_WARMING_UP
    ]
    assert packets
    assert len(packets) == len(values)
    for index, (packet, value) in enumerate(zip(packets, values, strict=True)):
        assert packet[:2] == b"\x02\x01"
        assert struct.unpack(">H", packet[2:4])[0] == 0xC000 | index
        assert struct.unpack(">f", packet[6:])[0] == pytest.approx(value, rel=1e-6)


def test_a_warming_up_device_sends_no_packet_and_another_stream_sends_none(loaded: Loaded) -> None:
    config = load_packet_config(CONFIG)
    twin = PacketTwin(config, "co2_ppm")
    quantity = Quantity(500.0, "ppm")
    assert (
        twin.packet_for(DeviceReading("sim_ndir_co2_cabin", "co2", quantity, 0.0, STATUS_WARMING_UP)) is None
    )
    assert (
        twin.packet_for(DeviceReading("sim_baro_cabin", "pressure", Quantity(101.0, "kPa"), 0.0, STATUS_OK))
        is None
    )
    assert twin.packet_for(DeviceReading("sim_ndir_co2_cabin", "co2", quantity, 0.0, STATUS_OK)) is not None
    with pytest.raises(PacketError):
        PacketTwin(config, "nope")
    with pytest.raises(ConfigError):
        twin.packet_for(DeviceReading("sim_ndir_co2_cabin", "co2", Quantity(500.0, "mmHg"), 0.0, STATUS_OK))


def test_a_config_edit_changes_the_document_and_the_packet(tmp_path: Path) -> None:
    path = tmp_path / "ccsds-packets.json"
    shutil.copy(CONFIG, path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["streams"][0]["apid"] = 700
    doc["streams"][0]["name"] = "cabin_co2"
    path.write_text(json.dumps(doc), encoding="utf-8")
    config = load_packet_config(path)
    assert b'value="700"' in xtce_document(config)
    assert b"cabin_co2_packet" in xtce_document(config)
    reading = DeviceReading("sim_ndir_co2_cabin", "co2", Quantity(1.5, "ppm"), 0.0, STATUS_OK)
    packet = PacketTwin(config, "cabin_co2").packet_for(reading)
    assert packet is not None
    assert packet[:2] == struct.pack(">H", 700)


def test_a_malformed_stream_config_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ccsds-packets.json"
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    first = doc["streams"][0]
    for index, change in ((0, {"apid": 0x800}), (1, {"apid": first["apid"]}), (1, {"name": first["name"]})):
        broken = json.loads(json.dumps(doc))
        broken["streams"][index].update(change)
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_packet_config(path)

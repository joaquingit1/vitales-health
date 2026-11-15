"""The Bluetooth Environmental Sensing twin: GATT bytes against the GATT Specification Supplement.

Expected bytes are worked by hand from the GSS structures (version date 2026-09-09), never from our
config: represented value = raw x 10^d with M = 1, b = 0, fields little endian (section 2.4, page 18).
- Temperature, section 3.235, Table 3.361: sint16, d = -2, range -273.15 to 327.67, 0x8000 not known.
- Humidity, section 3.134, Table 3.226: uint16, d = -2, range 0.00 to 100.00, 0xFFFF not known.
- Pressure, section 3.195, Table 3.303: uint32, d = -1, pascals.
UUIDs from Assigned Numbers (2025-03-13), sections 3.4.2 and 3.8.2. The decoder below is written from the
same tables, independently of the twin's encoder.
"""

import asyncio
import json
import shutil
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health_sim_habitat.app import Loaded, load_all
from crew_health_sim_habitat.ble_ess import (
    Characteristic,
    EssLinkError,
    EssTwin,
    ess_client_factory,
    load_ess_config,
)
from crew_health_sim_habitat.errors import ConfigError
from crew_health_sim_habitat.runner import build_devices, simulate

from conftest import CATALOG, REPO_ROOT

type Run = tuple[list[tuple[float, str, bytes]], dict[str, list[tuple[float, float]]]]
ESS_SERVICE = "0000181a-0000-1000-8000-00805f9b34fb"
TEMPERATURE = "00002a6e-0000-1000-8000-00805f9b34fb"
HUMIDITY = "00002a6f-0000-1000-8000-00805f9b34fb"
PRESSURE = "00002a6d-0000-1000-8000-00805f9b34fb"
# Worked by hand: 23.45 degC = 2345 = 0x0929; -273.15 = -27315 = 0x954D (two's complement); 327.67 = 0x7FFF.
TEMPERATURE_BYTES = {23.45: b"\x29\x09", -273.15: b"\x4d\x95", 327.67: b"\xff\x7f", None: b"\x00\x80"}
# 100.00 % = 10000 = 0x2710; 0.00 % = 0; 45.67 % = 4567 = 0x11D7.
HUMIDITY_BYTES = {100.0: b"\x10\x27", 0.0: b"\x00\x00", 45.67: b"\xd7\x11", None: b"\xff\xff"}
# 101325 Pa = 1013250 = 0x000F7602; 70300.5 Pa = 703005 = 0x000ABA1D.
PRESSURE_BYTES = {101325.0: b"\x02\x76\x0f\x00", 70300.5: b"\x1d\xba\x0a\x00"}


def decode(uuid: str, data: bytes) -> float | None:
    """A reader written from the GSS tables: None for 'value is not known'."""
    if uuid == TEMPERATURE:
        raw = int(struct.unpack("<h", data)[0])
        return None if raw == -0x8000 else raw / 100.0
    if uuid == HUMIDITY:
        raw = int(struct.unpack("<H", data)[0])
        return None if raw == 0xFFFF else raw / 100.0
    return int(struct.unpack("<I", data)[0]) / 10.0


def _char(loaded: Loaded, uuid: str) -> Characteristic:
    return next(c for c in loaded.ess.characteristics if c.uuid == uuid)


def test_uuids_are_the_assigned_numbers(loaded: Loaded) -> None:
    assert loaded.ess.service_uuid == ESS_SERVICE
    assert {c.uuid for c in loaded.ess.characteristics} == {TEMPERATURE, HUMIDITY, PRESSURE}


@pytest.mark.parametrize(
    ("uuid", "table"),
    [(TEMPERATURE, TEMPERATURE_BYTES), (HUMIDITY, HUMIDITY_BYTES), (PRESSURE, PRESSURE_BYTES)],
)
def test_encoding_matches_the_hand_worked_gss_values(
    loaded: Loaded, uuid: str, table: dict[float | None, bytes]
) -> None:
    char = _char(loaded, uuid)
    for value, expected in table.items():
        assert char.encode(value) == expected, value
        assert decode(uuid, expected) == value


def test_a_value_outside_the_allowed_range_is_sent_as_not_known(loaded: Loaded) -> None:
    assert _char(loaded, HUMIDITY).encode(100.01) == b"\xff\xff"
    assert _char(loaded, TEMPERATURE).encode(-300.0) == b"\x00\x80"


def _run(
    loaded: Loaded, scenario_id: str
) -> tuple[list[tuple[float, str, bytes]], dict[str, list[tuple[float, float]]]]:
    """Drive the twin from the simulated devices; return notifications and the device registers."""
    ess = loaded.ess.scenarios[scenario_id]
    base = loaded.scenario(ess.base_scenario)
    scenario = replace(base, duration_s=ess.duration_s)
    twin = EssTwin(loaded.ess, ess.faults)
    received: list[tuple[float, str, bytes]] = []
    registers: dict[str, list[tuple[float, float]]] = {c.uuid: [] for c in loaded.ess.characteristics}
    now = [0.0]

    async def subscribe() -> None:
        await twin.connect()
        for char in loaded.ess.characteristics:
            await twin.start_notify(
                char.uuid, lambda uuid, data: received.append((now[0], uuid, bytes(data)))
            )

    asyncio.run(subscribe())
    for tick in simulate(scenario, build_devices(scenario, loaded.wiring, loaded.kinds)):
        now[0] = tick.t_s
        twin.advance(tick.t_s, tick.readings)
        if not twin.is_connected and not any(
            f.fault == "disconnect" and f.active(tick.t_s) for f in ess.faults
        ):
            asyncio.run(subscribe())  # a client reconnects once the link is back
        for reading in tick.readings:
            for char in loaded.ess.characteristics:
                if (reading.device_id, reading.output) == (char.device_id, char.output):
                    scale = 1000.0 if reading.quantity.unit == "kPa" else 1.0
                    registers[char.uuid].append((tick.t_s, reading.quantity.value * scale))
    return received, registers


@pytest.fixture(scope="module")
def fault_run(loaded: Loaded) -> tuple[list[tuple[float, str, bytes]], dict[str, list[tuple[float, float]]]]:
    return _run(loaded, "ess_link_and_byte_faults")


def _window(loaded: Loaded, fault: str) -> tuple[float, float]:
    item = next(f for f in loaded.ess.scenarios["ess_link_and_byte_faults"].faults if f.fault == fault)
    return item.start_s, item.start_s + item.duration_s


def test_outside_faults_the_bytes_carry_the_device_registers(loaded: Loaded, fault_run: Run) -> None:
    received, registers = fault_run
    faulted = [_window(loaded, f) for f in ("disconnect", "out_of_range", "stuck", "drift")]
    by_time = {uuid: dict(rows) for uuid, rows in registers.items()}
    checked = 0
    for t_s, uuid, data in received:
        if any(start <= t_s < end for start, end in faulted):
            continue
        resolution = 0.1 if uuid == PRESSURE else 0.01
        assert decode(uuid, data) == pytest.approx(by_time[uuid][t_s], abs=resolution / 2 + 1e-9)
        checked += 1
    assert checked > 1000


def test_a_lost_link_stops_notifications_and_refuses_reads(loaded: Loaded, fault_run: Run) -> None:
    received, _ = fault_run
    start, end = _window(loaded, "disconnect")
    assert not [t for t, _, _ in received if start <= t < end]
    assert [t for t, _, _ in received if t >= end]
    twin = EssTwin(loaded.ess, loaded.ess.scenarios["ess_link_and_byte_faults"].faults)
    twin.advance(start, ())

    async def try_link() -> None:
        with pytest.raises(EssLinkError):
            await twin.connect()
        with pytest.raises(EssLinkError):
            await twin.read_gatt_char(TEMPERATURE)

    asyncio.run(try_link())


def test_out_of_range_sends_a_prohibited_humidity_code(loaded: Loaded, fault_run: Run) -> None:
    received, _ = fault_run
    start, end = _window(loaded, "out_of_range")
    codes = {struct.unpack("<H", d)[0] for t, u, d in received if u == HUMIDITY and start <= t < end}
    assert codes == {10450}  # 104.50 %: above the allowed 100.00 and not the 0xFFFF code, so prohibited


def test_stuck_repeats_the_same_bytes_while_the_register_moves(loaded: Loaded, fault_run: Run) -> None:
    received, registers = fault_run
    start, end = _window(loaded, "stuck")
    sent = {d for t, u, d in received if u == TEMPERATURE and start <= t < end}
    moved = {v for t, v in registers[TEMPERATURE] if start <= t < end}
    assert len(sent) == 1
    assert len(moved) > 1


def test_drift_adds_its_rate_on_the_wire(loaded: Loaded, fault_run: Run) -> None:
    received, registers = fault_run
    start, end = _window(loaded, "drift")
    rate_pa_per_h = 600.0  # the scenario's drift, typed here as the expectation
    by_time = dict(registers[PRESSURE])
    for t_s, uuid, data in received:
        if uuid == PRESSURE and start <= t_s < end:
            value = decode(uuid, data)
            assert value is not None
            assert value - by_time[t_s] == pytest.approx(rate_pa_per_h * (t_s - start) / 3600.0, abs=0.06)


def test_a_config_edit_changes_the_fault_bytes(tmp_path: Path) -> None:
    config = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "simulators" / "habitat" / "config", config)
    path = config / "ble-ess.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for fault in doc["fault_scenarios"][0]["faults"]:
        if fault["fault"] == "out_of_range":
            fault["value"]["value"] = 101.0
    path.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load_all(REPO_ROOT, config, CATALOG)
    fault = next(
        f for f in loaded.ess.scenarios["ess_link_and_byte_faults"].faults if f.fault == "out_of_range"
    )
    assert fault.value == 101.0


def test_a_fault_on_an_unknown_characteristic_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ble-ess.json"
    doc = json.loads(
        (REPO_ROOT / "simulators" / "habitat" / "config" / "ble-ess.json").read_text(encoding="utf-8")
    )
    doc["fault_scenarios"][0]["faults"][1]["characteristic"] = "co2"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_ess_config(path)


def test_the_factory_and_play_drive_a_client_like_a_ble_transport(loaded: Loaded) -> None:
    """The (address, disconnected_callback) factory shape of E07's BLE transport, played in an event loop."""
    ess = loaded.ess.scenarios["ess_link_and_byte_faults"]
    start, _ = _window(loaded, "disconnect")
    scenario = replace(loaded.scenario(ess.base_scenario), duration_s=start + 60.0)
    lost: list[EssTwin] = []
    received: list[str] = []
    twin = ess_client_factory(loaded.ess, ess.faults)(loaded.ess.twin_id, lost.append)

    async def session() -> None:
        await twin.connect()
        await twin.start_notify(HUMIDITY, lambda uuid, data: received.append(uuid))
        await twin.play(simulate(scenario, build_devices(scenario, loaded.wiring, loaded.kinds)))

    asyncio.run(session())
    assert twin.simulated
    assert received
    assert set(received) == {HUMIDITY}
    assert lost == [twin]
    assert not twin.is_connected
    with pytest.raises(EssLinkError):
        ess_client_factory(loaded.ess)("AA:BB:CC:DD:EE:FF", None)

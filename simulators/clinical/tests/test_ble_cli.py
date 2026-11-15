import asyncio
import json
import runpy
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crew_health_sim_clinical.__main__ import main
from crew_health_sim_clinical.ble import SimulatedBleClient, client_factory
from crew_health_sim_clinical.twin import Family, Notification, Twin, recording

from .test_twin import make


@pytest.mark.trace("T-E07-SIM-27", "E07-03")
@pytest.mark.parametrize("scenario", ["disconnect_mid_reading", "low_battery"])
def test_ble_byte_delivery_and_remote_disconnect(scenario: str) -> None:
    async def run() -> None:
        twin = make(scenario=scenario, interval="0")
        disconnected = asyncio.Event()
        observed: list[tuple[str, bytes]] = []
        calls: list[SimulatedBleClient] = []

        def lost(client: SimulatedBleClient) -> None:
            calls.append(client)
            disconnected.set()

        client = client_factory(twin)("SIM-address", disconnected_callback=lost)
        assert client.identity == twin.identity
        assert client.simulated is True
        assert not client.is_connected
        assert await client.connect()
        assert client.is_connected
        for name, uuid in twin.characteristics.items():

            def receive(sender: object, payload: bytearray, *, name: str = name) -> None:
                assert sender == twin.characteristics[name]
                assert isinstance(payload, bytearray)
                observed.append((name, bytes(payload)))

            await client.start_notify(uuid.upper(), receive)
        await asyncio.wait_for(disconnected.wait(), timeout=2)
        expected = [
            (event.characteristic, event.payload)
            for event in twin.events(20)
            if isinstance(event, Notification)
        ]
        assert observed == expected
        assert calls == [client]
        assert not client.is_connected
        assert await client.disconnect()
        assert await client.connect()
        assert client.is_connected
        assert await client.disconnect()

    asyncio.run(run())


@pytest.mark.trace("T-E07-SIM-28", "E07-03")
def test_ble_stop_and_cancel() -> None:
    async def run() -> None:
        twin = make(interval="0.000001")
        client = SimulatedBleClient("SIM", twin=twin)
        received = asyncio.Event()
        observed: list[bytes] = []
        calls: list[SimulatedBleClient] = []
        client = client_factory(twin)("SIM", calls.append)
        with pytest.raises(ValueError, match="disconnected"):
            await client.start_notify("plx_continuous", lambda _s, _p: None)
        await client.connect()
        with pytest.raises(ValueError, match="unknown characteristic"):
            await client.start_notify("unknown", lambda _s, _p: None)
        with pytest.raises(ValueError):
            await client.stop_notify("unknown")

        def receive(_sender: object, payload: bytearray) -> None:
            observed.append(bytes(payload))
            received.set()

        await client.start_notify("plx_continuous", receive)
        await client.start_notify("plx_continuous", receive)
        await asyncio.wait_for(received.wait(), timeout=2)
        await client.stop_notify("plx_continuous")
        await client.stop_notify("plx_continuous")
        before = list(observed)
        await client.disconnect()
        await asyncio.sleep(0)
        assert observed == before
        assert calls == []
        assert not client.is_connected

    asyncio.run(run())
    with pytest.raises(ValueError, match="address required"):
        client_factory(make())(" ")


@pytest.mark.trace("T-E07-SIM-29", "E07-03")
def test_ble_no_disconnect_callback_and_finite_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        twin = make(scenario="disconnect_mid_reading", interval="0")
        client = client_factory(twin)("SIM")
        await client.connect()
        await client.start_notify("plx_continuous", lambda _s, _p: None)
        task = client._task
        assert task is not None
        await asyncio.wait_for(task, timeout=2)
        assert not client.is_connected
        await client.disconnect()

        monkeypatch.setattr("crew_health_sim_clinical.ble.MAX_EVENTS", 2)
        normal = client_factory(make(interval="0"))("SIM")
        await normal.connect()
        await normal.start_notify("battery_level", lambda _s, _p: None)
        task = normal._task
        assert task is not None
        await asyncio.wait_for(task, timeout=2)
        assert normal._task is None
        await normal.disconnect()

    asyncio.run(run())


@pytest.mark.trace("T-E07-SIM-30", "E07-03")
def test_cli_record_and_list(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "record",
            "--family",
            "oximeter",
            "--scenario",
            "normal",
            "--seed",
            "1",
            "--count",
            "3",
            "--start",
            "2026-11-14T17:00:00Z",
            "--interval-s",
            "0",
        ]
    )
    value: object = json.loads(capsys.readouterr().out)
    assert value == recording(make(interval="0"), 3)
    main(["list"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 11
    assert lines[0].startswith("normal: ")
    main(["record", "--family", "cuff", "--scenario", "normal", "--seed", "1", "--count", "1"])
    assert '"origin": "SIM"' in capsys.readouterr().out


@pytest.mark.trace("T-E07-SIM-31", "E07-03")
@pytest.mark.parametrize(
    "args",
    [
        ["--interval-s", "bogus"],
        ["--interval-s", "-1"],
        ["--start", "bad"],
        ["--start", "2026-01-01"],
        ["--count", "-1"],
    ],
)
def test_cli_invalid_arguments(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["record", "--family", "cuff", "--scenario", "normal", "--seed", "1", "--count", "1", *args])
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.trace("T-E07-SIM-32", "E07-03")
def test_module_entry(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Remove the imported command module before run_module to avoid runpy's duplicate-module warning.
    monkeypatch.delitem(sys.modules, "crew_health_sim_clinical.__main__")
    monkeypatch.setattr(sys, "argv", ["crew_health_sim_clinical", "list"])
    runpy.run_module("crew_health_sim_clinical", run_name="__main__")
    assert "normal:" in capsys.readouterr().out


@pytest.mark.trace("T-E07-SIM-33", "E07-03")
def test_ble_single_characteristic_filters_and_cancels() -> None:
    async def run() -> None:
        twin = make("thermometer", interval="0")
        factory: Callable[..., SimulatedBleClient] = client_factory(twin)
        client = factory("SIM")
        await client.connect()
        done = asyncio.Event()
        data: list[bytes] = []

        def callback(_sender: object, payload: bytearray) -> None:
            data.append(bytes(payload))
            done.set()

        await client.start_notify("temperature_measurement", callback)
        await asyncio.wait_for(done.wait(), timeout=2)
        await client.disconnect()
        assert data[0] == next(event.payload for event in twin.events(1) if isinstance(event, Notification))
        assert twin.interval_s == Decimal(0)

    asyncio.run(run())


@pytest.mark.trace("T-E07-SIM-FROZEN-CLI", "E07-03")
@pytest.mark.parametrize("family", ["oximeter", "cuff", "thermometer"])
def test_cli_records_frozen_sensor(family: Family, capsys: pytest.CaptureFixture[str]) -> None:
    main(["record", "--family", family, "--scenario", "frozen_sensor", "--seed", "1", "--count", "8"])
    document: object = json.loads(capsys.readouterr().out)
    twin = Twin(family, "frozen_sensor", 1, start=datetime(2000, 1, 1, tzinfo=UTC), interval_s=Decimal(1))
    assert document == recording(twin, 8)
    main(["list"])
    assert "frozen_sensor:" in capsys.readouterr().out


@pytest.mark.trace("AGENTS 4", "E07-03")
def test_delivery_pace_is_separate_from_the_simulated_clock() -> None:
    twin = Twin("oximeter", "normal", 1, start=datetime(2000, 1, 1, tzinfo=UTC), interval_s=Decimal(1))
    received: list[bytes] = []

    async def run() -> None:
        client = client_factory(twin, pace_s=Decimal(0))("SIM-PLX-0001")
        await client.connect()
        await client.start_notify("plx_continuous", lambda _uuid, data: received.append(bytes(data)))
        for _ in range(200):
            if len(received) >= 3:
                break
            await asyncio.sleep(0)
        await client.disconnect()

    asyncio.run(run())
    assert len(received) >= 3
    with pytest.raises(ValueError, match="pace"):
        SimulatedBleClient("SIM", twin=twin, pace_s=Decimal(-1))

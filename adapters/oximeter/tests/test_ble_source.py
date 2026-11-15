"""BLE transport: the simulated twin by default (AGENTS.md section 4), a configured oximeter as the extra run."""

import asyncio
import os
import sys
import threading
import time
import types
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crew_health_sim_clinical.ble import client_factory
from crew_health_sim_clinical.twin import Twin

from crew_health_adapter_oximeter.family import CONTINUOUS, CONTINUOUS_UUID, SPOT_CHECK, SPOT_CHECK_UUID
from crew_health_adapter_oximeter.plx import PLXDecoder
from crew_health_device.adapters.ble_source import BleakFrameSource, BleClient
from crew_health_device.adapters.twin_source import parse_twin_spec, twin_source
from crew_health_device.application.ports import RawFrame, SourceDisconnected, SourceUnavailable
from crew_health_device.domain.errors import FrameError

UUIDS = {SPOT_CHECK: SPOT_CHECK_UUID, CONTINUOUS: CONTINUOUS_UUID}
FAST = Decimal("0.005")
START = datetime(2026, 11, 14, 9, tzinfo=UTC)


def twin_ble(scenario: str = "normal", *, max_queue: int = 64) -> BleakFrameSource:
    twin = Twin("oximeter", scenario, 7, start=START, interval_s=FAST)
    return BleakFrameSource(
        twin.identity.name, UUIDS, max_queue=max_queue, client_factory=client_factory(twin)
    )


def read_until_disconnect(source: BleakFrameSource) -> tuple[list[RawFrame], str]:
    frames: list[RawFrame] = []
    while True:
        try:
            frame = source.next_frame(2000)
        except SourceDisconnected as error:
            return frames, error.reason
        assert frame is not None, "the twin stopped notifying without a disconnect"
        frames.append(frame)


class StubClient:
    """A BleakClient-shaped client that is not a twin: no `simulated` identity, so the source stays LIVE."""

    def __init__(
        self, *, connect_delay: float = 0.0, fail_connect: bool = False, fail_disconnect: bool = False
    ):
        self.connect_delay, self.fail_connect, self.fail_disconnect = (
            connect_delay,
            fail_connect,
            fail_disconnect,
        )
        self.subscribed: list[str] = []
        self.callbacks: list[Callable[[object, bytearray], None]] = []
        self.on_disconnect: Callable[[object], None] | None = None

    async def connect(self) -> bool:
        await asyncio.sleep(self.connect_delay)
        if self.fail_connect:
            raise OSError("device not found")
        return True

    async def start_notify(self, char_specifier: str, callback: Callable[[object, bytearray], None]) -> None:
        self.subscribed.append(char_specifier)
        self.callbacks.append(callback)

    async def disconnect(self) -> bool:
        if self.fail_disconnect:
            raise OSError("link already gone")
        return True


def stub_source(client: StubClient) -> BleakFrameSource:
    def factory(address: str, on_disconnect: Callable[[object], None]) -> BleClient:
        client.on_disconnect = on_disconnect
        return client

    return BleakFrameSource("AA:BB", UUIDS, max_queue=4, client_factory=factory)


@pytest.mark.trace("E07-03", "R08", "AGENTS 4")
def test_twin_notifications_cross_the_ble_transport_and_decode_labeled_sim() -> None:
    source = twin_ble()
    try:
        source.open(2000)
        assert source.origin == "SIM"
        frames = [source.next_frame(2000) for _ in range(6)]
    finally:
        source.close()
    assert {frame.characteristic for frame in frames if frame is not None} == {CONTINUOUS, SPOT_CHECK}
    for frame in frames:
        assert frame is not None
        decoded = PLXDecoder().decode(frame.characteristic, frame.payload)
        assert decoded.characteristic == frame.characteristic


@pytest.mark.trace("E07-03", "R26", "AGENTS 4")
@pytest.mark.parametrize("scenario", ["disconnect_mid_reading", "low_battery"])
def test_link_loss_keeps_the_frames_received_before_it_then_reports_disconnected(scenario: str) -> None:
    source = twin_ble(scenario)
    source.open(2000)
    frames, reason = read_until_disconnect(source)
    source.close()
    # Both scenarios notify four measurements; the battery report is not a subscribed characteristic.
    assert len(frames) == 4
    assert reason == "disconnected"


@pytest.mark.trace("E07-03", "R08", "AGENTS 4")
def test_malformed_twin_packet_is_a_decoder_error_not_a_reading() -> None:
    source = twin_ble("malformed_packet")
    try:
        source.open(2000)
        frames = [source.next_frame(2000) for _ in range(3)]
    finally:
        source.close()
    errors = []
    for frame in frames:
        assert frame is not None
        try:
            PLXDecoder().decode(frame.characteristic, frame.payload)
        except FrameError as error:
            errors.append(error)
    assert len(errors) == 1


@pytest.mark.trace("E07-03", "R26")
def test_a_full_queue_ends_the_reading_as_overflow() -> None:
    source = twin_ble(max_queue=1)
    source.open(2000)
    time.sleep(0.1)
    with pytest.raises(SourceDisconnected) as error:
        source.next_frame(10)
    source.close()
    assert error.value.reason == "queue_overflow"


@pytest.mark.trace("E07-03", "R08")
def test_a_client_without_a_simulated_identity_is_live_and_quiet_means_no_frame() -> None:
    client = StubClient()
    source = stub_source(client)
    source.open(1000)
    assert source.origin == "LIVE"
    assert sorted(client.subscribed) == sorted(UUIDS.values())
    assert source.next_frame(10) is None
    source.close()


@pytest.mark.trace("E07-03", "R26")
def test_a_disconnect_while_waiting_reports_disconnected() -> None:
    client = StubClient()
    source = stub_source(client)
    source.open(1000)
    assert client.on_disconnect is not None
    timer = threading.Timer(0.02, client.on_disconnect, args=(client,))
    timer.start()
    with pytest.raises(SourceDisconnected) as error:
        source.next_frame(2000)
    timer.join()
    source.close()
    assert error.value.reason == "disconnected"


@pytest.mark.trace("E07-03", "R26")
def test_a_link_loss_on_a_full_queue_still_delivers_every_received_frame_first() -> None:
    client = StubClient()
    source = stub_source(client)
    source.open(1000)
    assert client.on_disconnect is not None
    for index in range(4):
        client.callbacks[0](client, bytearray([index]))
    client.on_disconnect(client)
    payloads = [frame.payload for frame in (source.next_frame(10) for _ in range(4)) if frame is not None]
    with pytest.raises(SourceDisconnected) as error:
        source.next_frame(10)
    source.close()
    assert payloads == [bytes([0]), bytes([1]), bytes([2]), bytes([3])]
    assert error.value.reason == "disconnected"


@pytest.mark.trace("E07-03", "R26")
@pytest.mark.parametrize(
    ("client", "reason"),
    [(StubClient(fail_connect=True), "device_absent"), (StubClient(connect_delay=5.0), "connect_timeout")],
)
def test_connection_failures_are_named(client: StubClient, reason: str) -> None:
    source = stub_source(client)
    with pytest.raises(SourceUnavailable) as error:
        source.open(50)
    source.close()
    assert error.value.reason == reason


@pytest.mark.trace("E07-03")
def test_close_reports_a_failed_disconnect_and_close_without_open_is_quiet() -> None:
    source = stub_source(StubClient(fail_disconnect=True))
    source.open(1000)
    with pytest.raises(SourceDisconnected):
        source.close()
    stub_source(StubClient()).close()


@pytest.mark.trace("E07-03", "AGENTS 3.7")
@pytest.mark.parametrize(
    ("address", "uuids", "max_queue"), [("", UUIDS, 4), ("AA", UUIDS, 0), ("AA", UUIDS, 4097), ("AA", {}, 4)]
)
def test_invalid_ble_configuration_is_refused(address: str, uuids: dict[str, str], max_queue: int) -> None:
    with pytest.raises(ValueError, match="invalid_ble_configuration"):
        BleakFrameSource(address, uuids, max_queue=max_queue)


@pytest.mark.trace("E07-03", "R26")
def test_without_the_ble_extra_the_radio_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "bleak", None)
    source = BleakFrameSource("AA:BB", UUIDS, max_queue=4)
    with pytest.raises(SourceUnavailable) as error:
        source.open(10)
    source.close()
    assert error.value.reason == "radio_absent"


@pytest.mark.trace("E07-03")
def test_the_default_factory_builds_a_bleak_client_with_the_disconnect_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[str, object]] = []

    class FakeBleakClient(StubClient):
        def __init__(self, address: str, disconnected_callback: Callable[[object], None]) -> None:
            super().__init__()
            created.append((address, disconnected_callback))

    monkeypatch.setitem(sys.modules, "bleak", types.SimpleNamespace(BleakClient=FakeBleakClient))
    source = BleakFrameSource("AA:BB", UUIDS, max_queue=4)
    source.open(1000)
    source.close()
    assert [address for address, _ in created] == ["AA:BB"]
    assert callable(created[0][1])


@pytest.mark.trace("AGENTS 4")
@pytest.mark.parametrize("spec", ["normal", "normal:", ":7", "normal:x", "normal:1234567890"])
def test_twin_spec_needs_a_scenario_and_a_bounded_seed(spec: str) -> None:
    with pytest.raises(ValueError, match="invalid_source"):
        parse_twin_spec(spec)


@pytest.mark.trace("AGENTS 4")
def test_an_unknown_or_inapplicable_scenario_fails_at_startup() -> None:
    with pytest.raises(ValueError):
        twin_source("oximeter", "storm:1", UUIDS, max_queue=4, start=lambda: START, interval_s=FAST)
    with pytest.raises(ValueError):
        twin_source("thermometer", "finger_out:1", UUIDS, max_queue=4, start=lambda: START, interval_s=FAST)


@pytest.mark.trace("AGENTS 4", "R08")
def test_each_acquisition_is_a_fresh_deterministic_reading_from_the_same_twin() -> None:
    assert parse_twin_spec("hypoxia_drop:7") == ("hypoxia_drop", 7)
    create = twin_source("oximeter", "normal:7", UUIDS, max_queue=8, start=lambda: START, interval_s=FAST)
    payloads: list[bytes] = []
    for _ in range(2):
        source = create()
        source.open(2000)
        assert source.origin == "SIM"
        frame = source.next_frame(2000)
        source.close()
        assert frame is not None
        payloads.append(frame.payload)
    assert payloads[0] != payloads[1]


@pytest.mark.trace("E07-03", "V15", "AGENTS 4")
@pytest.mark.hardware
def test_oximeter_over_ble_opens_and_yields_a_decodable_frame() -> None:
    """The twin by default; CHC_E07_OXIMETER_ADDRESS runs the same check against a paired device."""
    address = os.environ.get("CHC_E07_OXIMETER_ADDRESS")
    source = BleakFrameSource(address, UUIDS, max_queue=8) if address else twin_ble()
    try:
        source.open(10000)
        assert source.origin == ("LIVE" if address else "SIM")
        frame = source.next_frame(10000)
        assert frame is not None
        assert frame.characteristic in UUIDS
        PLXDecoder().decode(frame.characteristic, frame.payload)
    finally:
        source.close()

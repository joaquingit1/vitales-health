from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from crew_health_sim_clinical import gatt
from crew_health_sim_clinical.scenarios import load_scenarios
from crew_health_sim_clinical.twin import MAX_EVENTS, Disconnect, Family, Notification, Twin, recording

START = datetime(2026, 11, 14, 17, tzinfo=UTC)
D = Decimal


def make(family: Family = "oximeter", scenario: str = "normal", interval: str = "1", seed: int = 1) -> Twin:
    return Twin(family, scenario, seed, start=START, interval_s=D(interval))


def number(payload: bytes, offset: int, bits: int = 12, exp_bits: int = 4) -> Decimal:
    word = int.from_bytes(payload[offset : offset + (bits + exp_bits) // 8], "little")
    mantissa = word & ((1 << bits) - 1)
    if mantissa >= 1 << (bits - 1):
        mantissa -= 1 << bits
    exponent = word >> bits
    if exponent >= 1 << (exp_bits - 1):
        exponent -= 1 << exp_bits
    with localcontext() as context:
        context.prec = 300
        return D(mantissa) * D(10) ** exponent


def measurement(twin: Twin, count: int = 1) -> list[Notification]:
    return [event for event in twin.events(count) if isinstance(event, Notification)]


@pytest.mark.trace("T-E07-SIM-20", "E07-03")
@pytest.mark.parametrize("family", ["oximeter", "cuff", "thermometer"])
@pytest.mark.parametrize("scenario", list(load_scenarios()))
def test_determinism_and_clock(family: Family, scenario: str) -> None:
    if scenario == "finger_out" and family != "oximeter":
        with pytest.raises(ValueError):
            make(family, scenario)
        return
    twin = make(family, scenario)
    events = twin.events(8)
    assert events == make(family, scenario).events(8) == twin.events(8)
    assert twin.identity.simulated is True
    assert twin.identity.manufacturer == "crew-health simulator"
    assert twin.identity.model in ("sim-plx", "sim-bpm", "sim-htm")
    for index, event in enumerate(events):
        assert event.at == START + timedelta(seconds=index)
        if isinstance(event, Notification):
            assert event.uuid == twin.characteristics[event.characteristic]
            assert event.uuid == event.uuid.lower()
    assert twin.events(0) == ()


@pytest.mark.trace("T-E07-SIM-21", "E07-03")
def test_physiological_scenarios() -> None:
    normal = measurement(make())
    assert 0 <= number(normal[0].payload, 1) <= 100
    drop_twin = make(scenario="hypoxia_drop")
    count = int(drop_twin.scenario.parameters["samples"])
    drop = measurement(drop_twin, count + 3)
    values = [number(event.payload, 1) for event in drop]
    target = drop_twin.scenario.parameters["target_percent"]
    # The trajectory carries the same sensor noise as a resting reading: a falling trend, not a flat line.
    assert values[0] - values[-1] >= 10
    assert all(abs(value - target) <= 2 for value in values[-4:])
    assert len(set(values[-4:])) > 1
    base = measurement(make("thermometer"))[0]
    fever = measurement(make("thermometer", "fever"))[0]
    assert (
        number(fever.payload, 1, 24, 8) - number(base.payload, 1, 24, 8)
        == load_scenarios()["fever"].parameters["offset_cel"]
    )
    bp_base = measurement(make("cuff"))[0]
    bp_high = measurement(make("cuff", "hypertension"))[0]
    assert number(bp_high.payload, 1) > number(bp_base.payload, 1)
    assert number(bp_high.payload, 3) > number(bp_base.payload, 3)


@pytest.mark.trace("T-E07-SIM-22", "E07-03")
def test_fault_status_and_timestamps() -> None:
    for event in measurement(make(scenario="finger_out"), 2):
        assert event.payload[1:5] == bytes.fromhex("ff 07 ff 07")
        offset = 5 + (7 if event.characteristic == "plx_spot_check" else 0)
        assert int.from_bytes(event.payload[offset : offset + 2], "little") == 1 << 13
        assert int.from_bytes(event.payload[offset + 2 : offset + 5], "little") == 1 << 11
    for event in measurement(make(scenario="motion_low_perfusion"), 2):
        offset = 5 + (7 if event.characteristic == "plx_spot_check" else 0)
        assert int.from_bytes(event.payload[offset : offset + 2], "little") == 1 << 14
        assert int.from_bytes(event.payload[offset + 2 : offset + 5], "little") == (1 << 5) | (1 << 6)
    cuff = measurement(make("cuff", "motion_low_perfusion"))[0]
    assert cuff.payload[-2:] == b"\x01\x00"
    for family, offset in [("cuff", 7), ("thermometer", 5)]:
        event = measurement(make(family), 2)[1]  # type: ignore[arg-type]
        assert event.payload[offset : offset + 7] == gatt.date_time(event.at)
    spot = measurement(make(), 2)[1]
    assert spot.characteristic == "plx_spot_check"
    assert spot.payload[5:12] == gatt.date_time(spot.at)


@pytest.mark.trace("T-E07-SIM-23", "E07-03")
def test_out_of_range_malformed_and_disconnect() -> None:
    ox = measurement(make(scenario="out_of_range"))[0].payload
    assert number(ox, 1) == 110
    assert number(ox, 3) == 300
    temp = measurement(make("thermometer", "out_of_range"))[0].payload
    assert number(temp, 1, 24, 8) == 46
    bp = measurement(make("cuff", "out_of_range"))[0].payload
    assert number(bp, 1) == 320
    assert number(bp, 5) == D(227)
    for family in ("oximeter", "cuff", "thermometer"):
        good, bad = measurement(make(family), 4), measurement(make(family, "malformed_packet"), 4)
        assert bad[1].payload == good[1].payload[:-1]
        assert bad[0] == good[0] and bad[2:] == good[2:]
    for scenario in ("disconnect_mid_reading", "low_battery"):
        twin = make(scenario=scenario)
        events = twin.events(20)
        n = int(twin.scenario.parameters["notifications"])
        assert len(events) == n + 1 + int(scenario == "low_battery")
        assert isinstance(events[-1], Disconnect)
        if scenario == "low_battery":
            assert isinstance(events[0], Notification)
            assert events[0].payload == bytes((int(twin.scenario.parameters["percent"]),))
            assert events[0].characteristic == "battery_level"


@pytest.mark.trace("T-E07-SIM-24", "E07-03")
def test_recording_shape_and_identity() -> None:
    twin = make(scenario="low_battery")
    doc = recording(twin, 10)
    assert set(doc) == {"origin", "provenance", "frames"}
    assert doc["origin"] == "SIM"
    assert str(twin.seed) in str(doc["provenance"])
    assert "simulated twin, not a device recording" in str(doc["provenance"])
    assert doc["frames"] == [
        {"characteristic": event.characteristic, "payload_hex": event.payload.hex()}
        if isinstance(event, Notification)
        else {"event": "disconnect"}
        for event in twin.events(10)[1:]
    ]
    with pytest.raises(FrozenInstanceError):
        twin.identity.simulated = False  # type: ignore[misc]


@pytest.mark.trace("T-E07-SIM-25", "E07-03")
@pytest.mark.parametrize(
    "interval", ["-1", "NaN", "Infinity", "0.0000001", "1E20", "1.00000000000000000000000000001"]
)
def test_bad_interval(interval: str) -> None:
    with pytest.raises(ValueError):
        make(interval=interval)


@pytest.mark.trace("T-E07-SIM-26", "E07-03")
def test_invalid_arguments_and_clock_overflow() -> None:
    with pytest.raises(ValueError):
        make(scenario="missing")
    with pytest.raises(ValueError):
        Twin("unknown", "normal", 1, start=START, interval_s=D(0))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Twin("cuff", "normal", 1, start=START.replace(tzinfo=None), interval_s=D(1))
    with pytest.raises(ValueError):
        Twin(
            "cuff",
            "normal",
            1,
            start=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            interval_s=D(1),
        )
    for count in (-1, MAX_EVENTS + 1):
        with pytest.raises(ValueError):
            make().events(count)
    twin = Twin("cuff", "normal", 1, start=datetime.max.replace(tzinfo=UTC), interval_s=D(1))
    with pytest.raises(ValueError, match="clock overflow"):
        twin.events(2)


@pytest.mark.trace("T-E07-SIM-34", "E07-03")
def test_hypoxia_reaches_target_within_sample_count() -> None:
    twin = make(scenario="hypoxia_drop")
    n = int(twin.scenario.parameters["samples"])
    values = measurement(twin, n)
    assert abs(number(values[-1].payload, 1) - twin.scenario.parameters["target_percent"]) <= 2


@pytest.mark.trace("T-E07-SIM-35", "E07-03")
def test_single_sample_hypoxia_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    definitions = load_scenarios()
    definitions["hypoxia_drop"] = replace(
        definitions["hypoxia_drop"], parameters={"target_percent": D(80), "samples": D(1)}
    )
    monkeypatch.setattr("crew_health_sim_clinical.twin.load_scenarios", lambda: definitions)
    event = measurement(make(scenario="hypoxia_drop"))[0]
    assert number(event.payload, 1) == 80


@pytest.mark.trace("T-E07-SIM-FROZEN", "E07-03")
@pytest.mark.parametrize("family", ["oximeter", "cuff", "thermometer"])
def test_frozen_sensor_repeats_complete_payload_without_disconnect(family: Family) -> None:
    twin = make(family, "frozen_sensor")
    assert twin.scenario.parameters["from_notification"] == 2
    normal = measurement(make(family), 20)
    events = twin.events(20)
    assert len(events) == 20
    assert all(isinstance(event, Notification) for event in events)
    frames = measurement(twin, 20)
    assert frames[:3] == normal[:3]
    anchors = {event.characteristic: event.payload for event in frames[:3]}
    for index, event in enumerate(frames[2:], 2):
        assert event.payload == anchors[event.characteristic]
        assert event.at == START + timedelta(seconds=index)
    assert frames[0].payload != frames[2].payload
    if family == "oximeter":
        assert frames[3].payload == frames[1].payload
        assert frames[3].payload != normal[3].payload
        assert frames[3].payload[5:12] == gatt.date_time(frames[1].at)
    else:
        offset = 7 if family == "cuff" else 5
        assert frames[3].payload != normal[3].payload
        assert frames[3].payload[offset : offset + 7] == gatt.date_time(frames[2].at)
    assert twin.events(40)[:20] == events
    assert len(twin.events(40)) == 40


@pytest.mark.trace("T-E07-SIM-FROZEN-CONFIG", "E07-03")
@pytest.mark.parametrize("start_index", [0, 1, 4])
def test_frozen_sensor_uses_configured_index(monkeypatch: pytest.MonkeyPatch, start_index: int) -> None:
    from dataclasses import replace

    definitions = load_scenarios()
    definitions["frozen_sensor"] = replace(
        definitions["frozen_sensor"], parameters={"from_notification": D(start_index)}
    )
    monkeypatch.setattr("crew_health_sim_clinical.twin.load_scenarios", lambda: definitions)
    normal = measurement(make(), 12)
    frozen = measurement(make(scenario="frozen_sensor"), 12)
    anchors: dict[str, bytes] = {}
    for index, event in enumerate(frozen):
        if index <= start_index or event.characteristic not in anchors:
            anchors[event.characteristic] = normal[index].payload
        assert event.payload == anchors[event.characteristic]

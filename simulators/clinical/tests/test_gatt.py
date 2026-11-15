from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crew_health_sim_clinical import gatt
from crew_health_sim_clinical.ieee11073 import FLOAT_NAN, SFLOAT_NAN

AT = datetime(2026, 11, 14, 17, 2, 3, tzinfo=UTC)
D = Decimal


# Independent test-only decoder: field layouts are transcribed from the task's wire contract.
def decode_fields(payload: bytes, sizes: list[int]) -> list[int]:
    fields: list[int] = []
    cursor = 1
    for size in sizes:
        part = payload[cursor : cursor + size]
        if len(part) != size:
            raise ValueError("truncated field")
        fields.append(int.from_bytes(part, "little"))
        cursor += size
    if cursor != len(payload):
        raise ValueError("unexpected trailing bytes")
    return fields


def optional_sizes(flags: int, spec: list[tuple[int, list[int]]]) -> list[int]:
    return [size for bit, sizes in spec if flags & bit for size in sizes]


@pytest.mark.trace("T-E07-SIM-06", "E07-03")
def test_golden_vectors() -> None:
    # Device SFLOAT fields use whole units, with exponent zero.
    # Date: 2026 = 0x07EA, then month/day/hour/minute/second in plain bytes.
    assert gatt.plx_continuous(D(97), D(64)) == bytes.fromhex("00 61 00 40 00")
    assert gatt.plx_spot_check(D(97), D(64), timestamp=AT) == bytes.fromhex(
        "01 61 00 40 00 ea 07 0b 0e 11 02 03"
    )
    assert gatt.blood_pressure_measurement(D(118), D(76), D(90)) == bytes.fromhex("00 76 00 4c 00 5a 00")
    assert gatt.temperature_measurement(D("36.8")) == bytes.fromhex("00 70 01 00 ff")
    assert gatt.battery_level(5) == b"\x05"


@pytest.mark.trace("T-E07-SIM-07", "E07-03")
@pytest.mark.parametrize("flags", range(32))
def test_continuous_flags(flags: int) -> None:
    payload = gatt.plx_continuous(
        D(97),
        D(64),
        fast=(D(96), D(65)) if flags & 1 else None,
        slow=(D(98), D(63)) if flags & 2 else None,
        measurement_status=0x4000 if flags & 4 else None,
        device_status=0x8060 if flags & 8 else None,
        pai=D(1) if flags & 16 else None,
    )
    assert payload[0] == flags
    sizes = [2, 2] + optional_sizes(flags, [(1, [2, 2]), (2, [2, 2]), (4, [2]), (8, [3]), (16, [2])])
    expected = [0x0061, 0x0040]
    for bit, words in [
        (1, [0x0060, 0x0041]),
        (2, [0x0062, 0x003F]),
        (4, [0x4000]),
        (8, [0x8060]),
        (16, [0xF00A]),
    ]:
        if flags & bit:
            expected.extend(words)
    assert decode_fields(payload, sizes) == expected


@pytest.mark.trace("T-E07-SIM-08", "E07-03")
@pytest.mark.parametrize("flags", range(32))
def test_spot_flags(flags: int) -> None:
    payload = gatt.plx_spot_check(
        D(97),
        D(64),
        timestamp=AT if flags & 1 else None,
        measurement_status=0x2000 if flags & 2 else None,
        device_status=0x0800 if flags & 4 else None,
        pai=D(1) if flags & 8 else None,
        clock_not_set=bool(flags & 16),
    )
    assert payload[0] == flags
    sizes = [2, 2] + optional_sizes(flags, [(1, [2, 1, 1, 1, 1, 1]), (2, [2]), (4, [3]), (8, [2])])
    expected = [0x0061, 0x0040]
    for bit, words in [(1, [2026, 11, 14, 17, 2, 3]), (2, [0x2000]), (4, [0x0800]), (8, [0xF00A])]:
        if flags & bit:
            expected.extend(words)
    assert decode_fields(payload, sizes) == expected


@pytest.mark.trace("T-E07-SIM-09", "E07-03")
@pytest.mark.parametrize("flags", range(32))
def test_bp_flags(flags: int) -> None:
    payload = gatt.blood_pressure_measurement(
        D(118),
        D(76),
        D(90),
        kpa=bool(flags & 1),
        timestamp=AT if flags & 2 else None,
        pulse=D(64) if flags & 4 else None,
        user_id=255 if flags & 8 else None,
        measurement_status=0x2F if flags & 16 else None,
    )
    assert payload[0] == flags
    expected = [0x0076, 0x004C, 0x005A]
    for bit, words in [(2, [2026, 11, 14, 17, 2, 3]), (4, [0x0040]), (8, [255]), (16, [0x2F])]:
        if flags & bit:
            expected.extend(words)
    sizes = [2, 2, 2] + optional_sizes(flags, [(2, [2, 1, 1, 1, 1, 1]), (4, [2]), (8, [1]), (16, [2])])
    assert decode_fields(payload, sizes) == expected


@pytest.mark.trace("T-E07-SIM-10", "E07-03")
@pytest.mark.parametrize("flags", range(8))
def test_temperature_flags(flags: int) -> None:
    payload = gatt.temperature_measurement(
        D("36.8"),
        fahrenheit=bool(flags & 1),
        timestamp=AT if flags & 2 else None,
        temperature_type=9 if flags & 4 else None,
    )
    assert payload[0] == flags
    expected = [0xFF000170] + ([2026, 11, 14, 17, 2, 3] if flags & 2 else []) + ([9] if flags & 4 else [])
    sizes = [4] + optional_sizes(flags, [(2, [2, 1, 1, 1, 1, 1]), (4, [1])])
    assert decode_fields(payload, sizes) == expected


@pytest.mark.trace("T-E07-SIM-11", "E07-03")
def test_status_bits_and_special_payloads() -> None:
    assert [int(flag) for flag in gatt.MeasurementStatus] == [1 << bit for bit in range(5, 16)]
    assert [int(flag) for flag in gatt.DeviceSensorStatus] == [1 << bit for bit in range(16)]
    assert int(gatt.BloodPressureStatus.pulse_rate_exceeds_upper) == 8
    assert int(gatt.BloodPressureStatus.pulse_rate_below_lower) == 16
    for word in [0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801]:
        assert gatt.plx_continuous(word, word)[1:] == word.to_bytes(2, "little") * 2
    assert gatt.plx_spot_check(SFLOAT_NAN, SFLOAT_NAN)[1:] == bytes.fromhex("ff 07 ff 07")
    assert gatt.temperature_measurement(FLOAT_NAN)[1:] == bytes.fromhex("ff ff 7f 00")
    for percent in (0, 100):
        assert gatt.battery_level(percent) == bytes((percent,))


@pytest.mark.trace("T-E07-SIM-12", "E07-03")
@pytest.mark.parametrize(
    "encode",
    [
        lambda: gatt.plx_continuous(D(97), D(64), measurement_status=1),
        lambda: gatt.plx_spot_check(D(97), D(64), device_status=1 << 16),
        lambda: gatt.plx_continuous(D(97), D(64), device_status=-1),
        lambda: gatt.blood_pressure_measurement(D(118), D(76), D(90), measurement_status=0x18),
        lambda: gatt.blood_pressure_measurement(D(118), D(76), D(90), measurement_status=0x40),
        lambda: gatt.blood_pressure_measurement(D(118), D(76), D(90), user_id=256),
        lambda: gatt.temperature_measurement(D(37), temperature_type=0),
        lambda: gatt.temperature_measurement(D(37), temperature_type=10),
        lambda: gatt.battery_level(-1),
        lambda: gatt.battery_level(101),
    ],
)
def test_reject_invalid_fields(encode: Callable[[], bytes]) -> None:
    with pytest.raises(ValueError):
        encode()


@pytest.mark.trace("T-E07-SIM-DEVICE-FORM", "E07-03")
def test_device_resolution_golden_bytes() -> None:
    assert gatt.plx_continuous(D(98), D(64)) == bytes.fromhex("00 62 00 40 00")
    assert gatt.plx_spot_check(D(98), D(64)) == bytes.fromhex("00 62 00 40 00")
    assert gatt.temperature_measurement(D("36.6")) == bytes.fromhex("00 6e 01 00 ff")
    assert gatt.blood_pressure_measurement(D(120), D(80), D(93), pulse=D(64)) == bytes.fromhex(
        "04 78 00 50 00 5d 00 40 00"
    )


@pytest.mark.trace("T-E07-SIM-DEVICE-ROUNDING", "E07-03")
def test_device_resolution_rounds_half_even_before_encoding() -> None:
    assert gatt.plx_continuous(
        D("98.5"), D("65.5"), fast=(D("97.5"), D("64.5")), slow=(D("98.4"), D("65.6"))
    ) == bytes.fromhex("03 62 00 42 00 62 00 40 00 62 00 42 00")
    assert gatt.plx_spot_check(D("98.5"), D("65.5")) == bytes.fromhex("00 62 00 42 00")
    assert gatt.blood_pressure_measurement(
        D("120.5"), D("79.5"), D("92.5"), pulse=D("65.5")
    ) == bytes.fromhex("04 78 00 50 00 5c 00 42 00")
    assert gatt.temperature_measurement(D("36.65")) == bytes.fromhex("00 6e 01 00 ff")
    assert gatt.temperature_measurement(D("36.75")) == bytes.fromhex("00 70 01 00 ff")


@pytest.mark.trace("T-E07-SIM-DEVICE-OVERFLOW", "E07-03")
@pytest.mark.parametrize(
    "encode",
    [
        lambda: gatt.plx_continuous(D(3000), D(64)),
        lambda: gatt.blood_pressure_measurement(D(3000), D(80), D(93)),
        lambda: gatt.temperature_measurement(D("838860.6")),
        lambda: gatt.temperature_measurement(D("NaN")),
        lambda: gatt.temperature_measurement(D("Infinity")),
        lambda: gatt.temperature_measurement(D("1E999999")),
    ],
)
def test_device_resolution_rejects_unrepresentable_values(encode: Callable[[], bytes]) -> None:
    with pytest.raises(ValueError):
        encode()

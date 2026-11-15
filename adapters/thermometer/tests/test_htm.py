"""Independent Bluetooth FLOAT and HTM wire vectors."""

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health_adapter_thermometer.htm import HTMDecoder
from crew_health_device.domain.acquisition import ReceivedFrame, build_attempt
from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import DeviceDateTime, Special, decode_float
from crew_health_device.domain.readings import Decoder

from .helpers import Clock, binding, profile


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize("flags,unit", [(0, "degree_celsius"), (1, "degree_fahrenheit")])
def test_golden_temperature(flags: int, unit: str) -> None:
    # 0xFF00016E: exponent 0xFF = -1, mantissa 0x00016E = 366; 366 * 10^-1 = 36.6.
    decoder: Decoder = HTMDecoder()
    frame = decoder.decode("temperature_measurement", bytes([flags]) + bytes.fromhex("6e0100ff"))
    assert decoder.family == "thermometer"
    assert decoder.characteristics == frozenset(("temperature_measurement",))
    assert [(c.field, c.value, c.unit, c.missing) for c in frame.components] == [
        ("temperature", Decimal("36.6"), unit, None)
    ]
    assert frame.device_time is None and frame.device_clock_set is None
    assert frame.site_code is None and frame.user_index is None and not frame.status


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "raw,expected",
    [
        (0xFF00016E, Decimal("36.6")),  # (-1 << 24) | 366: 366 * 10^-1.
        (0xFFFFFFFF, Decimal("-0.1")),  # exponent -1; 24-bit signed 0xFFFFFF = -1.
        (0x00000000, Decimal("0")),  # exponent 0, mantissa 0: a measured zero, not missing.
        (0x017FFFFF, Decimal("83886070")),  # exponent 1; mantissa 2^23 - 1, not whole-value NaN.
        (0x00800003, Decimal("-8388605")),  # exponent 0; 0x800003 - 2^24.
    ],
)
def test_float_golden_signed_exponents_and_mantissas(raw: int, expected: Decimal) -> None:
    assert decode_float(raw) == expected
    assert (
        HTMDecoder()
        .decode("temperature_measurement", b"\x00" + raw.to_bytes(4, "little"))
        .components[0]
        .value
        == expected
    )


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "raw,reason",
    [
        (0x007FFFFF, Special.NOT_A_NUMBER),  # 2^23 - 1, exponent 0.
        (0x00800000, Special.NOT_AT_THIS_RESOLUTION),  # 2^23, exponent 0.
        (0x007FFFFE, Special.POSITIVE_INFINITY),  # 2^23 - 2, exponent 0.
        (0x00800002, Special.NEGATIVE_INFINITY),  # 2^23 + 2, exponent 0.
        (0x00800001, Special.RESERVED),  # 2^23 + 1, exponent 0.
    ],
)
def test_every_float_special_stays_missing(raw: int, reason: Special) -> None:
    assert decode_float(raw) is reason
    component = (
        HTMDecoder().decode("temperature_measurement", b"\x00" + raw.to_bytes(4, "little")).components[0]
    )
    assert component.value is None and component.missing is reason


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize("flags", range(8))
def test_all_optional_field_combinations_and_every_truncation(flags: int) -> None:
    # bits 0/1/2 select Fahrenheit/time/type; 0x07EA = 2026; site 9 is valid.
    payload = bytes([flags]) + bytes.fromhex("6e0100ff")
    if flags & 2:
        payload += bytes.fromhex("ea070b0e0c2238")
    if flags & 4:
        payload += b"\x09"
    frame = HTMDecoder().decode("temperature_measurement", payload)
    assert frame.site_code == (9 if flags & 4 else None)
    assert frame.device_time == (DeviceDateTime(2026, 11, 14, 12, 34, 56) if flags & 2 else None)
    assert frame.device_clock_set is None  # HTM supplies no clock-set status bit.
    for boundary in range(len(payload)):
        with pytest.raises(FrameError) as caught:
            HTMDecoder().decode("temperature_measurement", payload[:boundary])
        assert caught.value.reason == ("empty_frame" if boundary == 0 else "truncated")
    with pytest.raises(FrameError, match="trailing_bytes"):
        HTMDecoder().decode("temperature_measurement", payload + b"\x00")


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize("site", range(256))
def test_temperature_type_accepts_only_assigned_codes(site: int) -> None:
    payload = bytes.fromhex("046e0100ff") + bytes([site])
    if 1 <= site <= 9:
        assert HTMDecoder().decode("temperature_measurement", payload).site_code == site
    else:
        with pytest.raises(FrameError) as caught:
            HTMDecoder().decode("temperature_measurement", payload)
        assert caught.value.reason == "reserved_value"
        assert caught.value.field == "temperature_type"


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize("bit", range(3, 8))
@pytest.mark.parametrize("flags", range(8))
def test_each_rfu_flag_preserves_golden_values_and_optional_fields(bit: int, flags: int) -> None:
    payload = bytes([flags]) + bytes.fromhex("6e0100ff")
    if flags & 2:
        payload += bytes.fromhex("ea070b0e0c2238")
    if flags & 4:
        payload += b"\x09"
    golden = HTMDecoder().decode("temperature_measurement", payload)
    data = bytes([flags | 1 << bit]) + payload[1:]
    assert HTMDecoder().decode("temperature_measurement", data) == replace(
        golden, status=frozenset(("reserved_bits",))
    )
    with pytest.raises(FrameError, match="trailing_bytes"):
        HTMDecoder().decode("temperature_measurement", data + b"\x00")


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@given(st.integers(0, 31))
def test_combined_rfu_flags_preserve_golden_temperature(reserved: int) -> None:
    golden = HTMDecoder().decode("temperature_measurement", bytes.fromhex("006e0100ff"))
    payload = bytes([reserved << 3]) + bytes.fromhex("6e0100ff")
    status = frozenset(("reserved_bits",)) if reserved else frozenset()
    assert HTMDecoder().decode("temperature_measurement", payload) == replace(golden, status=status)


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
def test_rfu_status_reaches_attempt_quality_notes() -> None:
    now = Clock().now()
    frame = HTMDecoder().decode("temperature_measurement", bytes.fromhex("086e0100ff"))
    attempt = build_attempt(
        binding(),
        profile(),
        (ReceivedFrame(frame, now, 0),),
        started_at=now,
        ended_at=now,
        source_origin="SIM",
        end="window_complete",
    )
    assert attempt.outcome == "success"
    assert attempt.observations[0].value == Decimal("36.6")
    assert attempt.quality_notes == ("device_status:reserved_bits", "unchecked:rate:temperature")


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize("status_bit", range(16))
def test_htm_has_no_status_word_and_never_accepts_appended_status_bits(status_bit: int) -> None:
    # HTM defines no status field; even a single status bit is unadvertised trailing data.
    with pytest.raises(FrameError, match="trailing_bytes"):
        HTMDecoder().decode(
            "temperature_measurement", bytes.fromhex("006e0100ff") + (1 << status_bit).to_bytes(2, "little")
        )


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
def test_unknown_characteristic_and_invalid_timestamp() -> None:
    with pytest.raises(FrameError, match="unknown_characteristic"):
        HTMDecoder().decode("unknown", b"\x00")
    with pytest.raises(FrameError, match="invalid_timestamp"):
        HTMDecoder().decode("temperature_measurement", bytes.fromhex("026e0100ffea070d0e0c2238"))


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@settings(max_examples=500)
@given(st.sampled_from(("temperature_measurement", "unknown")), st.binary(max_size=512))
def test_arbitrary_bytes_only_raise_frame_error_or_return_finite_values(
    characteristic: str, payload: bytes
) -> None:
    try:
        frame = HTMDecoder().decode(characteristic, payload)
    except FrameError:
        return
    assert len(frame.components) == 1
    assert all(c.value is None or c.value.is_finite() for c in frame.components)


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@settings(max_examples=500)
@given(st.integers(min_value=0, max_value=0xFFFFFFFF), st.integers(min_value=0, max_value=7))
def test_structurally_valid_arbitrary_floats_never_leak_nonfinite(raw: int, flags: int) -> None:
    payload = bytes([flags]) + raw.to_bytes(4, "little")
    if flags & 2:
        payload += bytes(7)  # Unknown device time is explicitly permitted.
    if flags & 4:
        payload += b"\x01"
    frame = HTMDecoder().decode("temperature_measurement", payload)
    c = frame.components[0]
    assert (c.value is None) != (c.missing is None)
    assert c.value is None or c.value.is_finite()

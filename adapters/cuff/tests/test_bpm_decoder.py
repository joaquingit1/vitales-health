"""Hand-checked Bluetooth Blood Pressure Measurement vectors."""

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health_adapter_cuff.bpm import BPMDecoder
from crew_health_device.domain.acquisition import ReceivedFrame, build_attempt
from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import DeviceDateTime, Special
from crew_health_device.domain.readings import Decoder

from .helpers import Clock, binding, profile


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_minimal_golden_frame() -> None:
    decoder: Decoder = BPMDecoder()
    # Flags 0: mmHg; 0x0078 = 120 * 10^0, 0x0050 = 80, 0x005D = 93.
    frame = decoder.decode("blood_pressure_measurement", bytes.fromhex("00 78 00 50 00 5d 00"))
    assert decoder.family == "cuff"
    assert decoder.characteristics == frozenset(("blood_pressure_measurement",))
    assert frame.characteristic == "blood_pressure_measurement"
    assert [(c.field, c.value, c.unit, c.missing) for c in frame.components] == [
        ("systolic", Decimal(120), "millimetre_of_mercury", None),
        ("diastolic", Decimal(80), "millimetre_of_mercury", None),
        ("mean_arterial_pressure", Decimal(93), "millimetre_of_mercury", None),
    ]
    assert frame.status == frozenset()
    assert frame.device_time is None and frame.device_clock_set is None
    assert frame.user_index is None and frame.site_code is None


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_all_optional_fields_and_kpa_golden_frame() -> None:
    # Flags 0x1F = units | timestamp | pulse | user | status.
    # 0xF0A0 = 160 * 10^-1, 0xF06B = 107 * 10^-1, 0xF07C = 124 * 10^-1.
    # Year 0x07EA = 2026; pulse 0xF3E8 = 1000 * 10^-1; status 0x27 = bits 0,1,2,5.
    frame = BPMDecoder().decode(
        "blood_pressure_measurement",
        bytes.fromhex("1f a0 f0 6b f0 7c f0 ea 07 0b 0e 0c 22 38 e8 f3 02 27 00"),
    )
    assert [c.value for c in frame.components] == [
        Decimal("16.0"),
        Decimal("10.7"),
        Decimal("12.4"),
        Decimal("100.0"),
    ]
    assert [c.unit for c in frame.components] == ["kilopascal"] * 3 + ["beats_per_minute"]
    assert frame.device_time == DeviceDateTime(2026, 11, 14, 12, 34, 56)
    assert frame.device_clock_set is None and frame.site_code is None
    assert frame.user_index == 2
    assert frame.status == frozenset(
        ("body_movement", "cuff_too_loose", "irregular_pulse", "improper_measurement_position")
    )


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "status,name",
    [
        (0, None),
        (1, "body_movement"),
        (2, "cuff_too_loose"),
        (4, "irregular_pulse"),
        (8, "pulse_rate_exceeds_upper"),
        (16, "pulse_rate_below_lower"),
        (32, "improper_measurement_position"),
    ],
)
def test_each_status_bit_and_pulse_range(status: int, name: str | None) -> None:
    # Flag bit4 appends status; bits3..4 encode 00 normal, 01 upper, 10 lower.
    frame = BPMDecoder().decode(
        "blood_pressure_measurement", bytes.fromhex("10 78 00 50 00 5d 00") + status.to_bytes(2, "little")
    )
    assert frame.status == (frozenset() if name is None else frozenset((name,)))


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "payload", ["00 78 00 50 00 5d 00", "1f a0 f0 6b f0 7c f0 ea 07 0b 0e 0c 22 38 e8 f3 02 27 00"]
)
def test_every_truncation_boundary(payload: str) -> None:
    data = bytes.fromhex(payload)
    for boundary in range(len(data)):
        with pytest.raises(FrameError) as caught:
            BPMDecoder().decode("blood_pressure_measurement", data[:boundary])
        assert caught.value.reason == ("empty_frame" if boundary == 0 else "truncated")


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize("bit", range(5, 8))
def test_rfu_flag_bits(bit: int) -> None:
    payload = bytes.fromhex("1f a0 f0 6b f0 7c f0 ea 07 0b 0e 0c 22 38 e8 f3 02 27 00")
    golden = BPMDecoder().decode("blood_pressure_measurement", payload)
    data = bytes([payload[0] | 1 << bit]) + payload[1:]
    frame = BPMDecoder().decode("blood_pressure_measurement", data)
    assert frame == replace(golden, status=golden.status | {"reserved_bits"})
    with pytest.raises(FrameError, match="trailing_bytes"):
        BPMDecoder().decode("blood_pressure_measurement", data + b"\x00")


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize("status", [24, *(1 << bit for bit in range(6, 16))])
def test_rfu_status_and_reserved_pulse_range(status: int) -> None:
    # 0x18 is pulse-range bits 3 and 4 both set; bits6..15 are RFU.
    payload = bytes.fromhex("10 78 00 50 00 5d 00")
    golden = BPMDecoder().decode("blood_pressure_measurement", payload + bytes.fromhex("27 00"))
    frame = BPMDecoder().decode("blood_pressure_measurement", payload + (status | 0x27).to_bytes(2, "little"))
    assert frame == replace(golden, status=golden.status | {"reserved_bits"})


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@given(st.integers(0, 7), st.integers(0, 1023), st.integers(0, 3))
def test_combined_rfu_bits_preserve_pulse_range(flags: int, reserved: int, pulse_range: int) -> None:
    payload = bytes([0x10 | flags << 5]) + bytes.fromhex("780050005d00")
    payload += ((reserved << 6) | (pulse_range << 3)).to_bytes(2, "little")
    golden_status = (pulse_range << 3) if pulse_range != 3 else 0
    golden = BPMDecoder().decode(
        "blood_pressure_measurement", bytes.fromhex("10780050005d00") + golden_status.to_bytes(2, "little")
    )
    status = golden.status | {"reserved_bits"} if flags or reserved or pulse_range == 3 else golden.status
    assert BPMDecoder().decode("blood_pressure_measurement", payload) == replace(golden, status=status)


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize("payload", ["20780050005d00", "10780050005d004000", "10780050005d001800"])
def test_rfu_status_reaches_attempt_quality_notes(payload: str) -> None:
    now = Clock().now()
    frame = BPMDecoder().decode("blood_pressure_measurement", bytes.fromhex(payload))
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
    assert attempt.observations[0].value == Decimal(120)
    assert attempt.quality_notes == ("device_status:reserved_bits", "unchecked:rate:systolic_bp")


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "raw,special",
    [
        (0x07FF, Special.NOT_A_NUMBER),
        (0x0800, Special.NOT_AT_THIS_RESOLUTION),
        (0x07FE, Special.POSITIVE_INFINITY),
        (0x0802, Special.NEGATIVE_INFINITY),
        (0x0801, Special.RESERVED),
    ],
)
@pytest.mark.parametrize("component", range(4))
def test_every_special_in_every_numeric_field_is_missing(raw: int, special: Special, component: int) -> None:
    # Whole-word special encodings, never a numerical zero or an infinity.
    data = bytearray.fromhex("04 78 00 50 00 5d 00 48 00")
    offset = 1 + component * 2
    data[offset : offset + 2] = raw.to_bytes(2, "little")
    frame = BPMDecoder().decode("blood_pressure_measurement", bytes(data))
    assert frame.components[component].value is None
    assert frame.components[component].missing is special
    assert all(c.value is not None for index, c in enumerate(frame.components) if index != component)


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "payload,reason",
    [
        ("00 78 00 50 00 5d 00 00", "trailing_bytes"),
        ("02 78 00 50 00 5d 00 ea 07 0d 0e 0c 22 38", "invalid_timestamp"),
    ],
)
def test_trailing_bytes_and_invalid_timestamp(payload: str, reason: str) -> None:
    with pytest.raises(FrameError) as caught:
        BPMDecoder().decode("blood_pressure_measurement", bytes.fromhex(payload))
    assert caught.value.reason == reason


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_unknown_timestamp_and_signed_sfloat() -> None:
    # Flags bit1: timestamp; 0xFFFF = (-1) * 10^-1; year 0 means unknown.
    frame = BPMDecoder().decode(
        "blood_pressure_measurement", bytes.fromhex("02 ff ff 50 00 5d 00 00 00 0b 0e 0c 22 38")
    )
    assert frame.components[0].value == Decimal("-0.1")
    assert frame.device_time is None


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@settings(max_examples=500, derandomize=True)
@given(st.sampled_from(("blood_pressure_measurement", "unknown")), st.binary(max_size=512))
def test_arbitrary_bytes_only_raise_frame_error_or_produce_finite_values(
    characteristic: str, payload: bytes
) -> None:
    try:
        frame = BPMDecoder().decode(characteristic, payload)
    except FrameError:
        return
    assert all(
        (c.value is None and c.missing is not None)
        or (c.value is not None and c.value.is_finite() and c.missing is None)
        for c in frame.components
    )


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@given(
    st.integers(min_value=0, max_value=65535),
    st.integers(min_value=0, max_value=65535),
    st.integers(min_value=0, max_value=65535),
)
def test_structurally_valid_arbitrary_sfloat_frames_are_finite(a: int, b: int, c: int) -> None:
    payload = b"\x00" + b"".join(raw.to_bytes(2, "little") for raw in (a, b, c))
    frame = BPMDecoder().decode("blood_pressure_measurement", payload)
    assert all(component.value is None or component.value.is_finite() for component in frame.components)

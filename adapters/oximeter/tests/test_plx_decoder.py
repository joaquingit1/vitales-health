from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_adapter_oximeter.plx import PLXDecoder
from crew_health_device.domain.acquisition import ReceivedFrame, build_attempt
from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import DeviceDateTime, Special
from crew_health_device.domain.readings import Decoder

from .helpers import Clock, binding, profile


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
def test_continuous_minimal_matches_hand_checked_golden_frame() -> None:
    decoder: Decoder = PLXDecoder()
    # Flags 0; 0x0062 = 98 * 10^0; 0x0048 = 72 * 10^0.
    frame = decoder.decode("plx_continuous", bytes.fromhex("00 62 00 48 00"))
    assert decoder.family == "oximeter"
    assert [(c.field, c.value, c.unit) for c in frame.components] == [
        ("spo2", Decimal(98), "percent"),
        ("pulse_rate", Decimal(72), "beats_per_minute"),
    ]
    assert not frame.status
    assert frame.device_time is None and frame.device_clock_set is None


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
def test_continuous_fast_slow_status_sensor_and_amplitude_fields() -> None:
    # Flags 0x1F: Fast, Slow, status, sensor, PAI; all exponents 0.
    # Status 0x0180 sets bits 7 and 8; sensor 0x008010 sets bits 4 and 15.
    frame = PLXDecoder().decode(
        "plx_continuous", bytes.fromhex("1f 62 00 48 00 61 00 49 00 60 00 4a 00 80 01 10 80 00 05 00")
    )
    assert [(c.field, c.value) for c in frame.components] == [
        ("spo2", Decimal(98)),
        ("pulse_rate", Decimal(72)),
        ("spo2_fast", Decimal(97)),
        ("pulse_rate_fast", Decimal(73)),
        ("spo2_slow", Decimal(96)),
        ("pulse_rate_slow", Decimal(74)),
        ("pulse_amplitude_index", Decimal(5)),
    ]
    assert frame.status == frozenset(
        ("validated_data", "fully_qualified_data", "poor_signal", "sensor_disconnected")
    )


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@pytest.mark.parametrize("clock_flag,clock_set", [(0, True), (16, False)])
def test_spot_check_timestamp_and_clock_not_set(clock_flag: int, clock_set: bool) -> None:
    # Flags 0x0F: time, status, sensor, PAI; year 0x07EA = 2026.
    payload = bytes([15 | clock_flag]) + bytes.fromhex(
        "62 00 48 00 ea 07 0b 0e 0c 22 38 80 00 10 00 00 05 00"
    )
    frame = PLXDecoder().decode("plx_spot_check", payload)
    assert frame.device_time == DeviceDateTime(2026, 11, 14, 12, 34, 56)
    assert frame.device_clock_set is clock_set
    assert frame.status == frozenset(("validated_data", "poor_signal"))
    assert frame.components[-1].value == Decimal(5)


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "characteristic,payload",
    [
        ("plx_continuous", "1f 62 00 48 00 61 00 49 00 60 00 4a 00 80 01 10 80 00 05 00"),
        ("plx_spot_check", "0f 62 00 48 00 ea 07 0b 0e 0c 22 38 80 00 10 00 00 05 00"),
    ],
)
def test_truncation_at_each_boundary_is_a_typed_error(characteristic: str, payload: str) -> None:
    data = bytes.fromhex(payload)
    for boundary in range(1, len(data)):
        with pytest.raises(FrameError) as error:
            PLXDecoder().decode(characteristic, data[:boundary])
        assert error.value.reason == "truncated"
    with pytest.raises(FrameError, match="empty_frame"):
        PLXDecoder().decode(characteristic, b"")


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "characteristic,payload,reason",
    [
        ("plx_continuous", "00 62 00 48 00 ff", "trailing_bytes"),
        ("plx_spot_check", "00 62 00 48 00 ff", "trailing_bytes"),
        ("unknown", "00", "unknown_characteristic"),
    ],
)
def test_trailing_data_and_unknown_characteristics_are_never_accepted(
    characteristic: str, payload: str, reason: str
) -> None:
    with pytest.raises(FrameError) as error:
        PLXDecoder().decode(characteristic, bytes.fromhex(payload))
    assert error.value.reason == reason


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "characteristic,payload,status_offset,sensor_offset",
    [
        ("plx_continuous", "1f620048006100490060004a0080011080000500", 13, 15),
        ("plx_spot_check", "0f62004800ea070b0e0c223880001000000500", 12, 14),
    ],
)
@pytest.mark.parametrize(
    "field,bit",
    [("flags", b) for b in range(5, 8)]
    + [("status", b) for b in range(5)]
    + [("sensor", b) for b in range(16, 24)],
)
def test_rfu_bits_preserve_golden_values_and_known_status(
    characteristic: str, payload: str, status_offset: int, sensor_offset: int, field: str, bit: int
) -> None:
    data = bytearray.fromhex(payload)
    golden = PLXDecoder().decode(characteristic, bytes(data))
    offset = {"flags": 0, "status": status_offset, "sensor": sensor_offset}[field]
    data[offset + bit // 8] |= 1 << (bit % 8)
    frame = PLXDecoder().decode(characteristic, bytes(data))
    assert frame == replace(golden, status=golden.status | {"reserved_bits"})
    with pytest.raises(FrameError, match="trailing_bytes"):
        PLXDecoder().decode(characteristic, bytes(data) + b"\x00")


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@given(st.integers(0, 7), st.integers(0, 31), st.integers(0, 255))
def test_combined_rfu_fields_are_ignored(flags: int, measurement: int, sensor: int) -> None:
    payload = bytes([0x0C | flags << 5]) + bytes.fromhex("62004800")
    payload += measurement.to_bytes(2, "little") + (sensor << 16).to_bytes(3, "little")
    golden = PLXDecoder().decode("plx_continuous", bytes.fromhex("0c620048000000000000"))
    status = frozenset(("reserved_bits",)) if flags or measurement or sensor else frozenset()
    assert PLXDecoder().decode("plx_continuous", payload) == replace(golden, status=status)


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@pytest.mark.parametrize("payload", ["2062004800", "04620048000100", "0862004800000001"])
def test_rfu_status_reaches_attempt_quality_notes(payload: str) -> None:
    now = Clock().now()
    frame = PLXDecoder().decode("plx_continuous", bytes.fromhex(payload))
    attempt = build_attempt(
        binding(),
        profile(),
        (ReceivedFrame(frame, now, 0),),
        started_at=now,
        ended_at=now,
        source_origin="LIVE",
        end="window_complete",
    )
    assert attempt.outcome == "success"
    assert attempt.observations[0].value == Decimal(98)
    assert attempt.quality_notes == ("device_status:reserved_bits", "unchecked:rate:spo2")


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
def test_nan_spo2_is_typed_missing_never_zero() -> None:
    # Whole 16-bit 0x07FF denotes NaN; pulse 0x0048 remains independently measured.
    frame = PLXDecoder().decode("plx_continuous", bytes.fromhex("00 ff 07 48 00"))
    assert frame.components[0].value is None
    assert frame.components[0].missing is Special.NOT_A_NUMBER
    assert frame.components[1].value == Decimal(72)


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
@given(st.sampled_from(("plx_continuous", "plx_spot_check", "unknown")), st.binary(max_size=64))
def test_arbitrary_bytes_only_raise_frame_error_or_produce_finite_values(
    characteristic: str, payload: bytes
) -> None:
    try:
        frame = PLXDecoder().decode(characteristic, payload)
    except FrameError:
        return
    assert all(c.value is None or c.value.is_finite() for c in frame.components)

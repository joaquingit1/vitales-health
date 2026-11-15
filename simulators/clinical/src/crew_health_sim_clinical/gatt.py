"""Device-side Bluetooth SIG GATT Specification Supplement field encoders."""

from collections.abc import Iterable
from datetime import datetime
from enum import IntFlag

from .ieee11073 import WireValue, pack_float, pack_sfloat, unsigned_le

# Bluetooth SIG GATT Specification Supplement field resolutions: PLX SpO2 and
# pulse rate, BP pressure and pulse rate use whole units; temperature uses 0.1
# degree and Pulse Amplitude Index uses 0.1 percent. Non-NASA: the NASA catalog
# defines no device protocol. Exponents select this twin's fixed device form.
PLX_EXPONENT = 0
BLOOD_PRESSURE_EXPONENT = 0
TEMPERATURE_EXPONENT = -1
PAI_EXPONENT = -1


class MeasurementStatus(IntFlag):
    measurement_ongoing = 1 << 5
    early_estimated_data = 1 << 6
    validated_data = 1 << 7
    fully_qualified_data = 1 << 8
    data_from_storage = 1 << 9
    data_for_demonstration = 1 << 10
    data_for_testing = 1 << 11
    calibration_ongoing = 1 << 12
    measurement_unavailable = 1 << 13
    questionable_measurement = 1 << 14
    invalid_measurement = 1 << 15


class DeviceSensorStatus(IntFlag):
    extended_display_update_ongoing = 1 << 0
    equipment_malfunction = 1 << 1
    signal_processing_irregularity = 1 << 2
    inadequate_signal = 1 << 3
    poor_signal = 1 << 4
    low_perfusion = 1 << 5
    erratic_signal = 1 << 6
    nonpulsatile_signal = 1 << 7
    questionable_pulse = 1 << 8
    signal_analysis_ongoing = 1 << 9
    sensor_interference = 1 << 10
    sensor_unconnected_to_user = 1 << 11
    unknown_sensor_connected = 1 << 12
    sensor_displaced = 1 << 13
    sensor_malfunctioning = 1 << 14
    sensor_disconnected = 1 << 15


class BloodPressureStatus(IntFlag):
    body_movement = 1 << 0
    cuff_too_loose = 1 << 1
    irregular_pulse = 1 << 2
    pulse_rate_exceeds_upper = 1 << 3
    pulse_rate_below_lower = 2 << 3
    improper_measurement_position = 1 << 5


def date_time(value: datetime) -> bytes:
    return unsigned_le(value.year, 2) + bytes(
        (value.month, value.day, value.hour, value.minute, value.second)
    )


def _status(value: int, mask: int, size: int) -> bytes:
    if value < 0 or value & ~mask:
        raise ValueError("reserved status bits")
    return unsigned_le(value, size)


def _pair(values: tuple[WireValue, WireValue], exponent: int) -> bytes:
    return pack_sfloat(values[0], exponent) + pack_sfloat(values[1], exponent)


def _assemble(base_flags: int, fields: Iterable[tuple[int, bytes | None]]) -> bytes:
    payload = bytearray()
    for bit, field in fields:
        if field is not None:
            base_flags |= bit
            payload.extend(field)
    return bytes((base_flags,)) + payload


def plx_continuous(
    spo2: WireValue,
    pulse: WireValue,
    *,
    fast: tuple[WireValue, WireValue] | None = None,
    slow: tuple[WireValue, WireValue] | None = None,
    measurement_status: int | None = None,
    device_status: int | None = None,
    pai: WireValue | None = None,
) -> bytes:
    return _assemble(
        0,
        [
            (0, _pair((spo2, pulse), PLX_EXPONENT)),
            (1, None if fast is None else _pair(fast, PLX_EXPONENT)),
            (2, None if slow is None else _pair(slow, PLX_EXPONENT)),
            (4, None if measurement_status is None else _status(measurement_status, 0xFFE0, 2)),
            (8, None if device_status is None else _status(device_status, 0xFFFF, 3)),
            (16, None if pai is None else pack_sfloat(pai, PAI_EXPONENT)),
        ],
    )


def plx_spot_check(
    spo2: WireValue,
    pulse: WireValue,
    *,
    timestamp: datetime | None = None,
    measurement_status: int | None = None,
    device_status: int | None = None,
    pai: WireValue | None = None,
    clock_not_set: bool = False,
) -> bytes:
    return _assemble(
        16 if clock_not_set else 0,
        [
            (0, _pair((spo2, pulse), PLX_EXPONENT)),
            (1, None if timestamp is None else date_time(timestamp)),
            (2, None if measurement_status is None else _status(measurement_status, 0xFFE0, 2)),
            (4, None if device_status is None else _status(device_status, 0xFFFF, 3)),
            (8, None if pai is None else pack_sfloat(pai, PAI_EXPONENT)),
        ],
    )


def blood_pressure_measurement(
    systolic: WireValue,
    diastolic: WireValue,
    mean_arterial_pressure: WireValue,
    *,
    kpa: bool = False,
    timestamp: datetime | None = None,
    pulse: WireValue | None = None,
    user_id: int | None = None,
    measurement_status: int | None = None,
) -> bytes:
    if measurement_status is not None and measurement_status & 0x18 == 0x18:
        raise ValueError("reserved pulse rate range")
    return _assemble(
        int(kpa),
        [
            (
                0,
                _pair((systolic, diastolic), BLOOD_PRESSURE_EXPONENT)
                + pack_sfloat(mean_arterial_pressure, BLOOD_PRESSURE_EXPONENT),
            ),
            (2, None if timestamp is None else date_time(timestamp)),
            (4, None if pulse is None else pack_sfloat(pulse, BLOOD_PRESSURE_EXPONENT)),
            (8, None if user_id is None else unsigned_le(user_id, 1)),
            (16, None if measurement_status is None else _status(measurement_status, 0x3F, 2)),
        ],
    )


def temperature_measurement(
    value: WireValue,
    *,
    fahrenheit: bool = False,
    timestamp: datetime | None = None,
    temperature_type: int | None = None,
) -> bytes:
    if temperature_type is not None and not 1 <= temperature_type <= 9:
        raise ValueError("unknown temperature type")
    return _assemble(
        int(fahrenheit),
        [
            (0, pack_float(value, TEMPERATURE_EXPONENT)),
            (2, None if timestamp is None else date_time(timestamp)),
            (4, None if temperature_type is None else bytes((temperature_type,))),
        ],
    )


def battery_level(percent: int) -> bytes:
    if not 0 <= percent <= 100:
        raise ValueError("battery percent outside range")
    return bytes((percent,))

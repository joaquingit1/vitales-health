"""Bluetooth SIG Blood Pressure Service 0x1810, GATT Specification Supplement.

Blood Pressure Measurement 0x2A35 uses IEEE 11073-20601 SFLOAT values.
Protocol unit tokens identify the wire unit; only the supplied profile maps UCUM.
"""

from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import ByteReader, Special, decode_date_time, decode_sfloat
from crew_health_device.domain.readings import Component, DecodedFrame


def _component(reader: ByteReader, field: str, unit: str) -> Component:
    value = decode_sfloat(reader.u16())
    if isinstance(value, Special):
        return Component(field, None, value, unit)
    return Component(field, value, None, unit)


def _status(reader: ByteReader) -> frozenset[str]:
    raw = reader.u16()
    pulse_range = (raw >> 3) & 3
    names = (
        (0, "body_movement"),
        (1, "cuff_too_loose"),
        (2, "irregular_pulse"),
        (5, "improper_measurement_position"),
    )
    status = {name for bit, name in names if raw & (1 << bit)}
    # BPS RFU bits 6 to 15 and pulse range 3 do not change the fixed-width word.
    # Preserve known statuses without inventing an upper or lower pulse range.
    if raw & 0xFFC0 or pulse_range == 3:
        status.add("reserved_bits")
    if pulse_range == 1:
        status.add("pulse_rate_exceeds_upper")
    elif pulse_range == 2:
        status.add("pulse_rate_below_lower")
    return frozenset(status)


class BPMDecoder:
    @property
    def family(self) -> str:
        return "cuff"

    @property
    def characteristics(self) -> frozenset[str]:
        return frozenset(("blood_pressure_measurement",))

    def decode(self, characteristic: str, payload: bytes) -> DecodedFrame:
        if characteristic not in self.characteristics:
            raise FrameError("unknown_characteristic", characteristic)
        if not payload:
            raise FrameError("empty_frame")
        reader = ByteReader(payload)
        flags = reader.u8()
        unit = "kilopascal" if flags & 1 else "millimetre_of_mercury"
        components = tuple(
            _component(reader, field, unit) for field in ("systolic", "diastolic", "mean_arterial_pressure")
        )
        device_time = decode_date_time(reader.take(7)) if flags & 2 else None
        if flags & 4:
            components += (_component(reader, "pulse_rate", "beats_per_minute"),)
        user_index = reader.u8() if flags & 8 else None
        if user_index == 0xFF:
            user_index = None
        status = _status(reader) if flags & 0x10 else frozenset()
        # BPS flags 5 to 7 are RFU and define no optional fields; ignore them.
        # finish still rejects bytes outside the known field layout.
        if flags & 0xE0:
            status |= {"reserved_bits"}
        reader.finish()
        return DecodedFrame(characteristic, components, status, device_time, None, user_index, None)

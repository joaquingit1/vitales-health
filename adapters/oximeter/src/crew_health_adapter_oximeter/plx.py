"""Bluetooth SIG PLX 0x1822 wire decoder, GATT Specification Supplement."""

from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import ByteReader, Special, decode_date_time, decode_sfloat
from crew_health_device.domain.readings import Component, DecodedFrame


def _component(reader: ByteReader, field: str, unit: str) -> Component:
    value = decode_sfloat(reader.u16())
    if isinstance(value, Special):
        return Component(field, None, value, unit)
    return Component(field, value, None, unit)


def _pair(reader: ByteReader, suffix: str = "") -> tuple[Component, Component]:
    return (
        _component(reader, "spo2" + suffix, "percent"),
        _component(reader, "pulse_rate" + suffix, "beats_per_minute"),
    )


def _status(reader: ByteReader, measurement: bool, sensor: bool) -> frozenset[str]:
    statuses: set[str] = set()
    if measurement:
        raw = reader.u16()
        if raw & 0x1F:
            # GATT Supplement: RFU bits 0 to 4 do not change this fixed-width status word.
            statuses.add("reserved_bits")
        names: tuple[str, ...] = (
            "measurement_ongoing",
            "early_estimated_data",
            "validated_data",
            "fully_qualified_data",
            "data_from_storage",
            "data_for_demonstration",
            "data_for_testing",
            "calibration_ongoing",
            "measurement_unavailable",
            "questionable_measurement",
            "invalid_measurement",
        )
        statuses.update(name for bit, name in enumerate(names, start=5) if raw & (1 << bit))
    if sensor:
        raw = reader.u24()
        if raw & 0xFF0000:
            # GATT Supplement: RFU bits 16 to 23 do not change this fixed-width status word.
            statuses.add("reserved_bits")
        names = (
            "extended_display_update_ongoing",
            "equipment_malfunction",
            "signal_processing_irregularity",
            "inadequate_signal",
            "poor_signal",
            "low_perfusion",
            "erratic_signal",
            "nonpulsatile_signal",
            "questionable_pulse",
            "signal_analysis_ongoing",
            "sensor_interference",
            "sensor_unconnected_to_user",
            "unknown_sensor_connected",
            "sensor_displaced",
            "sensor_malfunctioning",
            "sensor_disconnected",
        )
        statuses.update(name for bit, name in enumerate(names) if raw & (1 << bit))
    return frozenset(statuses)


class PLXDecoder:
    @property
    def family(self) -> str:
        return "oximeter"

    @property
    def characteristics(self) -> frozenset[str]:
        return frozenset(("plx_spot_check", "plx_continuous"))

    def decode(self, characteristic: str, payload: bytes) -> DecodedFrame:
        if characteristic not in self.characteristics:
            raise FrameError("unknown_characteristic", characteristic)
        if not payload:
            raise FrameError("empty_frame")
        reader = ByteReader(payload)
        flags = reader.u8()
        components: tuple[Component, ...] = _pair(reader)
        device_time = None
        clock_set = None
        if characteristic == "plx_spot_check":
            if flags & 1:
                device_time = decode_date_time(reader.take(7))
                clock_set = not bool(flags & 0x10)
            status = _status(reader, bool(flags & 2), bool(flags & 4))
            if flags & 8:
                components += (_component(reader, "pulse_amplitude_index", "percent"),)
        else:
            if flags & 1:
                components += _pair(reader, "_fast")
            if flags & 2:
                components += _pair(reader, "_slow")
            status = _status(reader, bool(flags & 4), bool(flags & 8))
            if flags & 0x10:
                components += (_component(reader, "pulse_amplitude_index", "percent"),)
        # PLX flags 5 to 7 are RFU in both layouts and define no optional fields.
        # Ignore them; finish still rejects bytes outside the known field layout.
        if flags & 0xE0:
            status |= {"reserved_bits"}
        reader.finish()
        return DecodedFrame(characteristic, components, status, device_time, clock_set, None, None)


PlxDecoder = PLXDecoder

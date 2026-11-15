"""Bluetooth SIG Health Thermometer 0x1809 decoder, GATT Specification Supplement."""

from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import ByteReader, Special, decode_date_time, decode_float
from crew_health_device.domain.readings import Component, DecodedFrame


class HTMDecoder:
    @property
    def family(self) -> str:
        return "thermometer"

    @property
    def characteristics(self) -> frozenset[str]:
        return frozenset(("temperature_measurement",))

    def decode(self, characteristic: str, payload: bytes) -> DecodedFrame:
        if characteristic not in self.characteristics:
            raise FrameError("unknown_characteristic", characteristic)
        if not payload:
            raise FrameError("empty_frame")
        reader = ByteReader(payload)
        flags = reader.u8()
        # HTS flags 3 to 7 are RFU and define no optional fields; ignore them.
        # finish still rejects bytes outside the known field layout.
        status = frozenset(("reserved_bits",)) if flags & 0xF8 else frozenset()
        unit = "degree_fahrenheit" if flags & 1 else "degree_celsius"
        value = decode_float(reader.u32())
        component = (
            Component("temperature", None, value, unit)
            if isinstance(value, Special)
            else Component("temperature", value, None, unit)
        )
        device_time = decode_date_time(reader.take(7)) if flags & 2 else None
        site = reader.u8() if flags & 4 else None
        if site is not None and not 1 <= site <= 9:
            raise FrameError("reserved_value", "temperature_type")
        reader.finish()
        # HTM has no clock-set indicator or measurement status word. Device time is untrusted.
        return DecodedFrame(characteristic, (component,), status, device_time, None, None, site)

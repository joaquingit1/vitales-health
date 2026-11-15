"""IEEE 11073-20601 SFLOAT/FLOAT per Bluetooth SIG GATT Specification Supplement."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .errors import FrameError


class Special(StrEnum):
    NOT_A_NUMBER = "not_a_number"
    NOT_AT_THIS_RESOLUTION = "not_at_this_resolution"
    POSITIVE_INFINITY = "positive_infinity"
    NEGATIVE_INFINITY = "negative_infinity"
    RESERVED = "reserved"


def _decode(raw: int, bits: int, mantissa_bits: int) -> Decimal | Special:
    if type(raw) is not int or not 0 <= raw < 1 << bits:
        raise ValueError("outside_wire_width")
    midpoint = 1 << (mantissa_bits - 1)
    special = (
        (midpoint - 2, Special.POSITIVE_INFINITY),
        (midpoint - 1, Special.NOT_A_NUMBER),
        (midpoint, Special.NOT_AT_THIS_RESOLUTION),
        (midpoint + 1, Special.RESERVED),
        (midpoint + 2, Special.NEGATIVE_INFINITY),
    )
    for encoded, reason in special:
        if raw == encoded:
            return reason
    mantissa = raw & ((1 << mantissa_bits) - 1)
    if mantissa & midpoint:
        mantissa -= 1 << mantissa_bits
    exponent = raw >> mantissa_bits
    exponent_bits = bits - mantissa_bits
    if exponent & (1 << (exponent_bits - 1)):
        exponent -= 1 << exponent_bits
    return Decimal(mantissa).scaleb(exponent)


def decode_sfloat(raw: int) -> Decimal | Special:
    return _decode(raw, 16, 12)


def decode_float(raw: int) -> Decimal | Special:
    return _decode(raw, 32, 24)


@dataclass(frozen=True, slots=True)
class DeviceDateTime:
    year: int
    month: int
    day: int
    hours: int
    minutes: int
    seconds: int


class ByteReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._position

    def take(self, n: int) -> bytes:
        if n < 0 or n > self.remaining:
            raise FrameError("truncated")
        result = self._data[self._position : self._position + n]
        self._position += n
        return result

    def u8(self) -> int:
        return int.from_bytes(self.take(1), "little")

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u24(self) -> int:
        return int.from_bytes(self.take(3), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def finish(self) -> None:
        if self.remaining:
            raise FrameError("trailing_bytes")


def decode_date_time(data: bytes) -> DeviceDateTime | None:
    reader = ByteReader(data)
    result = DeviceDateTime(reader.u16(), reader.u8(), reader.u8(), reader.u8(), reader.u8(), reader.u8())
    reader.finish()
    if result.year == 0 or result.month == 0 or result.day == 0:
        return None
    if not 1582 <= result.year <= 9999 or result.month > 12 or result.day > 31:
        raise FrameError("invalid_timestamp")
    if result.hours > 23 or result.minutes > 59 or result.seconds > 59:
        raise FrameError("invalid_timestamp")
    return result

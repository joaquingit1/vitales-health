from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_device.domain.errors import FrameError
from crew_health_device.domain.ieee11073 import (
    ByteReader,
    DeviceDateTime,
    Special,
    decode_date_time,
    decode_float,
    decode_sfloat,
)


@pytest.mark.trace("E07-03", "V15")
@pytest.mark.parametrize("raw,value", [(0x0062, "98"), (0xF3E8, "100.0"), (0xFFFF, "-0.1")])
def test_sfloat_specification_vectors(raw: int, value: str) -> None:
    # 0x0062: 98 * 10^0; 0xF3E8: 1000 * 10^-1; 0xFFFF: -1 * 10^-1.
    assert decode_sfloat(raw) == Decimal(value)


@pytest.mark.trace("E07-03", "V15")
def test_float_specification_vector() -> None:
    # Signed exponent 0xFF = -1; mantissa 0x00016E = 366.
    assert decode_float(0xFF00016E) == Decimal("36.6")


@pytest.mark.trace("E07-03", "V15")
@pytest.mark.parametrize(
    "offset,special",
    [
        (0, Special.POSITIVE_INFINITY),
        (1, Special.NOT_A_NUMBER),
        (2, Special.NOT_AT_THIS_RESOLUTION),
        (3, Special.RESERVED),
        (4, Special.NEGATIVE_INFINITY),
    ],
)
def test_special_values_are_typed_missing_reasons(offset: int, special: Special) -> None:
    assert decode_sfloat(0x07FE + offset) is special
    assert decode_float(0x007FFFFE + offset) is special


@pytest.mark.trace("E07-03", "V15")
@given(st.integers(min_value=0, max_value=0xFFFF))
def test_sfloat_exact_signed_mantissa_and_exponent(raw: int) -> None:
    value = decode_sfloat(raw)
    if isinstance(value, Special):
        assert raw in (0x07FE, 0x07FF, 0x0800, 0x0801, 0x0802)
    else:
        mantissa = raw & 0xFFF
        exponent = raw >> 12
        mantissa = mantissa - 0x1000 if mantissa & 0x800 else mantissa
        exponent = exponent - 16 if exponent & 8 else exponent
        assert value == Decimal(mantissa).scaleb(exponent)
        assert value.is_finite()


@pytest.mark.trace("E07-03", "V15")
@given(st.integers(min_value=0, max_value=0xFFFFFFFF))
def test_float_exact_signed_mantissa_and_exponent(raw: int) -> None:
    value = decode_float(raw)
    if isinstance(value, Special):
        assert raw in (0x007FFFFE, 0x007FFFFF, 0x00800000, 0x00800001, 0x00800002)
    else:
        mantissa = raw & 0xFFFFFF
        exponent = raw >> 24
        mantissa = mantissa - 0x1000000 if mantissa & 0x800000 else mantissa
        exponent = exponent - 256 if exponent & 128 else exponent
        assert value == Decimal(mantissa).scaleb(exponent)
        assert value.is_finite()


@pytest.mark.trace("E07-03", "V15")
@pytest.mark.parametrize("raw", [-1, 0x100000000])
def test_numeric_decoders_reject_values_outside_wire_width(raw: int) -> None:
    with pytest.raises(ValueError):
        decode_sfloat(raw)
    with pytest.raises(ValueError):
        decode_float(raw)


@pytest.mark.trace("E07-03", "V15")
def test_date_time_unknown_and_known() -> None:
    assert decode_date_time(bytes.fromhex("ea 07 0b 0e 0c 22 38")) == DeviceDateTime(2026, 11, 14, 12, 34, 56)
    for payload in ("00 00 0b 0e 0c 22 38", "ea 07 00 0e 0c 22 38", "ea 07 0b 00 0c 22 38"):
        assert decode_date_time(bytes.fromhex(payload)) is None


@pytest.mark.trace("E07-03", "V15")
@pytest.mark.parametrize(
    "payload",
    [
        "2d 06 01 01 00 00 00",
        "10 27 01 01 00 00 00",
        "ea 07 0d 01 00 00 00",
        "ea 07 01 20 00 00 00",
        "ea 07 01 01 18 00 00",
        "ea 07 01 01 00 3c 00",
        "ea 07 01 01 00 00 3c",
    ],
)
def test_invalid_date_time_raises_typed_frame_error(payload: str) -> None:
    with pytest.raises(FrameError, match="invalid_timestamp"):
        decode_date_time(bytes.fromhex(payload))


@pytest.mark.trace("E07-03", "V15")
def test_byte_reader_bounds_and_little_endian_values() -> None:
    reader = ByteReader(bytes.fromhex("01 02 03 04 05 06 07 08 09 0a"))
    assert (reader.u8(), reader.u16(), reader.u24(), reader.u32()) == (1, 0x0302, 0x060504, 0x0A090807)
    assert reader.remaining == 0
    reader.finish()
    with pytest.raises(FrameError, match="truncated"):
        reader.u8()
    with pytest.raises(FrameError, match="trailing_bytes"):
        ByteReader(b"1").finish()
    with pytest.raises(FrameError, match="truncated"):
        decode_date_time(b"123")


@pytest.mark.trace("E07-03", "V15")
def test_special_tokens_require_the_entire_wire_word() -> None:
    # 0xF7FF: exponent -1 and mantissa 2047, unlike whole-word NaN 0x07FF.
    assert decode_sfloat(0xF7FF) == Decimal("204.7")
    # 0xFF7FFFFF: exponent -1 and mantissa 8388607, unlike 0x007FFFFF.
    assert decode_float(0xFF7FFFFF) == Decimal("838860.7")


@pytest.mark.trace("E07-03", "V15")
def test_component_requires_exactly_one_finite_value_or_special() -> None:
    from crew_health_device.domain.readings import Component

    with pytest.raises(ValueError, match="exactly_one"):
        Component("spo2", None, None, "percent")
    with pytest.raises(ValueError, match="exactly_one"):
        Component("spo2", Decimal(0), Special.NOT_A_NUMBER, "percent")
    with pytest.raises(ValueError, match="non_finite"):
        Component("spo2", Decimal("NaN"), None, "percent")

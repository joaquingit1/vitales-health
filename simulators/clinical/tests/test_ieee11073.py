from decimal import Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_sim_clinical import ieee11073 as ieee


def decode(word: int, bits: int, exponent_bits: int) -> Decimal:
    mask = (1 << bits) - 1
    mantissa = word & mask
    if mantissa & (1 << (bits - 1)):
        mantissa -= 1 << bits
    exponent = word >> bits
    if exponent & (1 << (exponent_bits - 1)):
        exponent -= 1 << exponent_bits
    with localcontext() as context:
        context.prec = 300
        return Decimal(mantissa) * Decimal(10) ** exponent


@pytest.mark.trace("T-E07-SIM-01", "E07-03")
@pytest.mark.parametrize("value,word", [("98", 0x0062), ("64", 0x0040), ("0", 0x0000), ("-1", 0x0FFF)])
def test_sfloat_golden(value: str, word: int) -> None:
    assert ieee.encode_sfloat(Decimal(value), 0) == word


@pytest.mark.trace("T-E07-SIM-02", "E07-03")
@given(st.integers(-2045, 2045), st.integers(-8, 7))
def test_sfloat_exact(mantissa: int, exponent: int) -> None:
    value = Decimal(mantissa).scaleb(exponent)
    word = ieee.encode_sfloat(value, exponent)
    assert decode(word, 12, 4) == value
    assert word >> 12 == exponent & 0xF


@pytest.mark.trace("T-E07-SIM-03", "E07-03")
@given(st.integers(-8388605, 8388605), st.integers(-128, 127))
def test_float_exact(mantissa: int, exponent: int) -> None:
    value = Decimal(mantissa).scaleb(exponent)
    word = ieee.encode_float(value, exponent)
    assert decode(word, 24, 8) == value
    assert word >> 24 == exponent & 0xFF


@pytest.mark.trace("T-E07-SIM-04", "E07-03")
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1E-200", "1E200", "2047", "-2048"])
def test_unrepresentable(value: str) -> None:
    with pytest.raises(ValueError):
        ieee.encode_sfloat(Decimal(value), 0)


@pytest.mark.trace("T-E07-SIM-05", "E07-03")
def test_special_words_and_float_golden() -> None:
    assert ieee.encode_float(Decimal("36.6"), -1) == 0xFF00016E
    for prefix, words, size in [
        ("SFLOAT", [0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801], 2),
        ("FLOAT", [0x007FFFFF, 0x00800000, 0x007FFFFE, 0x00800002, 0x00800001], 4),
    ]:
        for suffix, word in zip(["NAN", "NRES", "POS_INF", "NEG_INF", "RESERVED"], words, strict=True):
            assert getattr(ieee, prefix + "_" + suffix) == word
            pack = ieee.pack_sfloat if size == 2 else ieee.pack_float
            assert pack(word, -1) == word.to_bytes(size, "little")
    with pytest.raises(ValueError):
        ieee.encode_float(Decimal("1E-129"), -1)
    with pytest.raises(ValueError):
        ieee.pack_sfloat(-1, 0)


@pytest.mark.trace("T-E07-SIM-ENCODING-BOUNDS", "E07-03")
@pytest.mark.parametrize("exponent", [-129, 128])
def test_float_rejects_exponent_outside_wire_range(exponent: int) -> None:
    with pytest.raises(ValueError):
        ieee.encode_float(Decimal(0), exponent)


@pytest.mark.trace("T-E07-SIM-ENCODING-BOUNDS", "E07-03")
@pytest.mark.parametrize("exponent", [-9, 8])
def test_sfloat_rejects_exponent_outside_wire_range(exponent: int) -> None:
    with pytest.raises(ValueError):
        ieee.encode_sfloat(Decimal(0), exponent)


@pytest.mark.trace("T-E07-SIM-ENCODING-BOUNDS", "E07-03")
@pytest.mark.parametrize("value", ["0.1", "2046", "-2046", "1E999999", "1E-999999"])
def test_sfloat_rejects_unrepresentable_at_selected_exponent(value: str) -> None:
    with pytest.raises(ValueError):
        ieee.encode_sfloat(Decimal(value), 0)


@pytest.mark.trace("T-E07-SIM-ENCODING-BOUNDS", "E07-03")
@pytest.mark.parametrize("value", ["NaN", "Infinity", "8388606", "-8388606", "0.01"])
def test_float_rejects_unrepresentable_at_selected_exponent(value: str) -> None:
    with pytest.raises(ValueError):
        ieee.encode_float(Decimal(value), 0)

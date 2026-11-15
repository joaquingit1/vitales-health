"""IEEE 11073 decimal words. Integer packing inputs are explicit raw wire words."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

SFLOAT_NAN = 0x07FF
SFLOAT_NRES = 0x0800
SFLOAT_POS_INF = 0x07FE
SFLOAT_NEG_INF = 0x0802
SFLOAT_RESERVED = 0x0801
FLOAT_NAN = 0x007FFFFF
FLOAT_NRES = 0x00800000
FLOAT_POS_INF = 0x007FFFFE
FLOAT_NEG_INF = 0x00800002
FLOAT_RESERVED = 0x00800001

type WireValue = Decimal | int


def _encode(value: Decimal, exponent: int, mantissa_bits: int, exponent_bits: int) -> int:
    minimum_exponent = -(1 << (exponent_bits - 1))
    maximum_exponent = -minimum_exponent - 1
    limit = (1 << (mantissa_bits - 1)) - 3
    if not minimum_exponent <= exponent <= maximum_exponent:
        raise ValueError("exponent outside wire range")
    if not value.is_finite():
        raise ValueError("use an explicit special wire word")
    if value and not exponent <= value.adjusted() < exponent + len(str(limit)):
        raise ValueError("decimal outside wire range")
    numerator, denominator = value.as_integer_ratio()
    dividend: int = numerator * 10 ** max(0, -exponent)
    divisor: int = denominator * 10 ** max(0, exponent)
    mantissa, remainder = divmod(dividend, divisor)
    if remainder or not -limit <= mantissa <= limit:
        raise ValueError("decimal cannot be represented exactly")
    return ((exponent & ((1 << exponent_bits) - 1)) << mantissa_bits) | (
        mantissa & ((1 << mantissa_bits) - 1)
    )


def encode_sfloat(value: Decimal, exponent: int) -> int:
    """Return the exact word at the explicitly selected exponent."""
    return _encode(value, exponent, 12, 4)


def encode_float(value: Decimal, exponent: int) -> int:
    return _encode(value, exponent, 24, 8)


def unsigned_le(value: int, size: int) -> bytes:
    if not 0 <= value < 1 << (size * 8):
        raise ValueError("unsigned field outside wire range")
    return value.to_bytes(size, "little")


def _quantize(value: Decimal, exponent: int) -> Decimal:
    with localcontext() as context:
        context.prec = 32
        try:
            return value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError("decimal outside wire range") from exc


def pack_sfloat(value: WireValue, exponent: int) -> bytes:
    word = encode_sfloat(_quantize(value, exponent), exponent) if isinstance(value, Decimal) else value
    return unsigned_le(word, 2)


def pack_float(value: WireValue, exponent: int) -> bytes:
    word = encode_float(_quantize(value, exponent), exponent) if isinstance(value, Decimal) else value
    return unsigned_le(word, 4)

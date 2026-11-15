"""Exact supply quantities (E32 3 and DD1): Decimal values, the unit in the type, no coercion between units."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal, InvalidOperation, localcontext

from crew_health.kernel.units import Quantity, Unit
from crew_health.supplies.domain.errors import UnitMismatch

PRECISION = 34  # E32 3: Decimal context precision 34
MAX_COVER = 2**53  # E32-T-22: a cover past the exact integer range of a JSON number is refused
_ANNOTATION_CHARS = frozenset(chr(c) for c in range(0x21, 0x7F)) - {"{", "}"}


def annotation_unit(word: str) -> Unit | None:
    """A kit-map unit word as a UCUM annotation unit `{word}` (E32 3: one mechanical rule, no table).

    None when the word cannot be an annotation (empty, a space, a brace or a non printable character): S-L7."""
    if not word or any(ch not in _ANNOTATION_CHARS for ch in word):
        return None
    return Unit("{" + word + "}")


def boundary_unit(unit: Unit, word: str, own: Unit) -> Unit:
    """A caller's unit at the module boundary: the bare kit-map word and its annotation are the same unit."""
    return own if unit.code == word else unit


def same_unit(left: Quantity, right: Quantity) -> None:
    if left.unit != right.unit:
        raise UnitMismatch(left.unit.code, right.unit.code)


def zero(unit: Unit) -> Quantity:
    return Quantity(Decimal(0), unit)


def add(left: Quantity, right: Quantity) -> Quantity:
    same_unit(left, right)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return Quantity(left.value + right.value, left.unit)


def subtract(left: Quantity, right: Quantity) -> Quantity:
    same_unit(left, right)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return Quantity(left.value - right.value, left.unit)


def floor_ratio(numerator: Quantity, denominator: Quantity) -> int:
    """floor(numerator / denominator) exactly; both non negative, denominator above zero."""
    same_unit(numerator, denominator)
    if denominator.value <= 0 or numerator.value < 0:
        raise ValueError("floor_ratio needs a non negative numerator and a positive denominator")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        quotient = (numerator.value / denominator.value).to_integral_value(rounding=ROUND_FLOOR)
        # Guard the one inexact case: a rounded quotient one above the exact floor.
        if quotient * denominator.value > numerator.value:
            quotient -= 1
    return int(quotient)


def scale_of(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def within_bounds(value: Decimal, *, maximum: Decimal, max_scale: int) -> bool:
    """A positive finite amount no larger than the bound and with no more decimal places than allowed (E32 6.1)."""
    return value.is_finite() and Decimal(0) < value <= maximum and scale_of(value.normalize()) <= max_scale


def decimal_text(value: Decimal) -> str:
    """Canonical decimal text, never an exponent: what SQLite stores and the API renders (E32 5.1)."""
    if not value.is_finite():
        raise ValueError("non finite decimal")
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0", "") else text


def parse_decimal_text(text: str) -> Decimal:
    """Strict inverse of decimal_text; anything else is a corrupt stored value."""
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("not a decimal") from error
    if not value.is_finite() or decimal_text(value) != text:
        raise ValueError("not canonical decimal text")
    return value

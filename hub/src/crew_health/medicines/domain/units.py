"""UCUM amounts for dosing (E32 3.5 step 2 and 8): exact decimal conversion inside one dimension, ISMP display.

Mass converts to mass and volume to volume; an arbitrary unit (`{tbl}`, `{puff}`) converts only to itself, so a dose
never silently changes meaning (`unit_incompatible`)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

_MASS: Mapping[str, Decimal] = {
    "kg": Decimal(1000000),
    "g": Decimal(1000),
    "mg": Decimal(1),
    "ug": Decimal("0.001"),
}
_VOLUME: Mapping[str, Decimal] = {"L": Decimal(1000), "mL": Decimal(1)}
_DISPLAY: Mapping[str, str] = {"ug": "mcg", "{tbl}": "tablet", "{dose}": "dose"}


class UnitIncompatible(ValueError):
    """Two units of different dimensions, or an arbitrary unit against another unit."""


@dataclass(frozen=True, slots=True)
class Amount:
    value: Decimal
    ucum: str

    def __post_init__(self) -> None:
        if not self.value.is_finite():
            raise ValueError("an amount is finite")


def known(ucum: str) -> bool:
    return ucum in _MASS or ucum in _VOLUME or (ucum.startswith("{") and ucum.endswith("}") and len(ucum) > 2)


def _factor(ucum: str) -> tuple[str, Decimal]:
    if ucum in _MASS:
        return "mass", _MASS[ucum]
    if ucum in _VOLUME:
        return "volume", _VOLUME[ucum]
    if known(ucum):
        return ucum, Decimal(1)
    raise UnitIncompatible(f"unknown unit {ucum}")


def convert(amount: Amount, to: str) -> Amount:
    """Exact conversion; raises UnitIncompatible across dimensions or for an arbitrary unit."""
    source, source_factor = _factor(amount.ucum)
    target, target_factor = _factor(to)
    if source != target:
        raise UnitIncompatible(f"{amount.ucum} does not convert to {to}")
    with localcontext() as ctx:
        ctx.prec = 34
        return Amount(amount.value * source_factor / target_factor, to)


def text(value: Decimal) -> str:
    """ISMP number: leading zero, no trailing zero, thousands separators."""
    normal = value.normalize()
    if normal == normal.to_integral_value():
        normal = normal.quantize(Decimal(1))
    whole, _, fraction = f"{normal:f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-") or "0"
    grouped = f"{int(digits):,}"
    return sign + grouped + ("." + fraction if fraction else "")


def display(amount: Amount, words: Mapping[str, str] | None = None) -> str:
    """ISMP: number, a space, then the unit word (`mcg`, never `ug`); a count word is plural above one."""
    word = (words or {}).get(amount.ucum) or _DISPLAY.get(amount.ucum) or amount.ucum
    if amount.ucum.startswith("{") and amount.value != 1:
        word = word + "s"
    return f"{text(amount.value)} {word}"

"""UCUM unit registry (SYSTEM 5.4): ucumvert parses the case-sensitive UCUM code and pint converts exact Decimals.

Only this adapter touches ucumvert or pint; the kernel sees `Unit` and `Quantity`. The registry loads once at startup
(AGENTS 3.3). A code ucumvert cannot parse is INVALID_UNIT, and a conversion between dimensions is INVALID_UNIT: no
label is ever read heuristically and no unit is guessed."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, cast

from ucumvert import PintUcumRegistry

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity, Unit


class _Units(Protocol):
    @property
    def dimensionality(self) -> object: ...


class _Measure(Protocol):
    @property
    def units(self) -> _Units: ...
    @property
    def magnitude(self) -> object: ...
    def to(self, units: _Units) -> _Measure: ...


class _Registry(Protocol):
    def from_ucum(self, code: str) -> _Measure: ...
    def Quantity(self, value: Decimal, units: _Units) -> _Measure: ...  # noqa: N802 (pint's name)


def _invalid_unit() -> ProblemError:
    return ProblemError(ErrorCode.INVALID_UNIT, ErrorCode.INVALID_UNIT.value)


class UcumUnitRegistry:
    def __init__(self, *, max_cached: int) -> None:
        if max_cached < 1:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        self._registry = cast(_Registry, PintUcumRegistry(non_int_type=Decimal))
        self._max_cached = max_cached
        self._cache: dict[str, _Units] = {}

    def _units(self, code: str) -> _Units:
        cached = self._cache.get(code)
        if cached is not None:
            return cached
        if not isinstance(code, str) or not code or code != code.strip():
            raise _invalid_unit()
        try:
            units = self._registry.from_ucum(code).units
        except Exception:  # ucumvert raises its own parse errors and pint's; every one means "not UCUM"
            raise _invalid_unit() from None
        if len(self._cache) < self._max_cached:
            self._cache[code] = units
        return units

    def parse(self, code: str) -> Unit:
        self._units(code)
        return Unit(code)

    def commensurable(self, a: Unit, b: Unit) -> bool:
        return self._units(a.code).dimensionality == self._units(b.code).dimensionality

    def convert(self, quantity: Quantity, to: Unit) -> Quantity:
        if not self.commensurable(quantity.unit, to):
            raise _invalid_unit()
        if quantity.unit.code == to.code:
            return quantity
        magnitude = (
            self._registry.Quantity(quantity.value, self._units(quantity.unit.code))
            .to(self._units(to.code))
            .magnitude
        )
        if not isinstance(magnitude, Decimal | int):
            raise _invalid_unit()
        return Quantity(Decimal(magnitude), to)

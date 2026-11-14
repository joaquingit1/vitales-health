from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.kernel.clock import require_utc

if TYPE_CHECKING:
    from crew_health.kernel.config import ReleaseHandle


@dataclass(frozen=True, slots=True)
class Unit:
    code: str


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: Unit

    def __post_init__(self) -> None:
        _finite(self.value)


@dataclass(frozen=True, slots=True)
class WireQuantity:
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        _finite(self.value)


@dataclass(frozen=True, slots=True)
class PressureContext:
    pressure: Quantity
    basis: Literal["measured", "assumed"]  # internal enum
    source_ref: str
    observed_at: datetime | None

    def __post_init__(self) -> None:
        if self.observed_at is not None:
            require_utc(self.observed_at)


@dataclass(frozen=True, slots=True)
class ConvertedQuantity:
    original: WireQuantity
    canonical: Quantity
    conversion: str
    mapping_version: str
    pressure: PressureContext | None
    rounding: str


class UnitRegistry(Protocol):
    def parse(self, code: str) -> Unit: ...
    def convert(self, quantity: Quantity, to: Unit) -> Quantity: ...
    def commensurable(self, a: Unit, b: Unit) -> bool: ...


class WireUnitConverter(Protocol):
    def convert(
        self,
        value: WireQuantity,
        *,
        indicator_code: str,
        release: ReleaseHandle,
        to: Unit,
        pressure: PressureContext | None = None,
    ) -> ConvertedQuantity: ...


def _finite(value: Decimal) -> None:
    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.errors import ProblemError

    if not isinstance(value, Decimal) or not value.is_finite():
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)

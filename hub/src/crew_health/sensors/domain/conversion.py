"""Wire to canonical conversion through the S5.4 converter (E23 3.8 step 4, D12)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.units import (
    ConvertedQuantity,
    PressureContext,
    Quantity,
    Unit,
    WireQuantity,
    WireUnitConverter,
)
from crew_health.sensors.domain.channel import Conversion, SensorTypeFacts
from crew_health.sensors.domain.errors import InvalidUnit, InvalidValue
from crew_health.sensors.domain.reading import require_finite, round_wire


@dataclass(frozen=True, slots=True)
class WireConversion:
    """The `conversion` object of a stored reading; None for identity (E23 3.8 step 4)."""

    method: Literal["scale", "ppm_to_mmhg"]
    factor: Decimal | None
    cabin_pressure: Quantity | None
    pressure_basis: Literal["measured", "assumed"] | None
    pressure_source_ref: str | None


@dataclass(frozen=True, slots=True)
class CanonicalReading:
    """Unrounded canonical value for evaluation, four place value for the wire, originals kept."""

    converted: ConvertedQuantity
    wire_value: Decimal
    conversion: WireConversion | None


def _usable_pressure(pressure: PressureContext | None) -> PressureContext:
    # DE23-6: no pressure supplied by the reading means rejection; no default is ever supplied here.
    if pressure is None or pressure.pressure.value <= 0 or not pressure.source_ref:
        raise InvalidValue("ppm conversion needs a positive sourced cabin pressure")
    return pressure


def _wire_conversion(
    method: Conversion, factor: Decimal | None, pressure: PressureContext | None
) -> WireConversion | None:
    if method == "identity":
        return None
    if method == "scale":
        return WireConversion("scale", factor, None, None, None)
    context = _usable_pressure(pressure)
    return WireConversion("ppm_to_mmhg", None, context.pressure, context.basis, context.source_ref)


def convert_wire_reading(
    converter: WireUnitConverter,
    release: ReleaseHandle,
    sensor_type: SensorTypeFacts,
    *,
    value: Decimal,
    unit: str,
    canonical_unit: Unit,
    pressure: PressureContext | None,
) -> CanonicalReading:
    """Convert one reading. The unit must be the catalog unit or an accepted unit (INVALID_UNIT)."""
    require_finite(value)
    accepted = sensor_type.accepted_unit(unit)
    if accepted is None:
        raise InvalidUnit(unit)
    record = _wire_conversion(accepted.conversion, accepted.factor, pressure)
    converted = converter.convert(
        WireQuantity(value, unit),
        indicator_code=sensor_type.id,
        release=release,
        to=canonical_unit,
        pressure=pressure if accepted.conversion == "ppm_to_mmhg" else None,
    )
    if converted.canonical.unit != canonical_unit:
        raise InvalidUnit("converter returned another unit")
    require_finite(converted.canonical.value)
    return CanonicalReading(converted, round_wire(converted.canonical.value), record)

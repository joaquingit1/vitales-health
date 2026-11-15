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
from crew_health.sensors.domain.channel import AcceptedUnit, Conversion, SensorTypeFacts
from crew_health.sensors.domain.errors import InvalidUnit, InvalidValue
from crew_health.sensors.domain.reading import arithmetic, decimal_scope, require_finite, round_wire


@dataclass(frozen=True, slots=True)
class WireConversion:
    """The `conversion` object of a stored reading; None for identity (E23 3.8 step 4)."""

    method: Literal["scale", "ppm_to_mmhg", "ppo2_to_pio2"]
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
    return WireConversion(method, None, context.pressure, context.basis, context.source_ref)


def _pio2(ppo2: Decimal, accepted: AcceptedUnit, pressure: PressureContext) -> Decimal:
    """PIO2 = ppO2 x (PB - PH2O) / PB (EV01 Table 6.2-1, p.51; EV06 separates ppO2 = PB x FiO2 from PIO2).

    An oxygen cell's kPa reading is the ambient ppO2; the inspired value the limits are written in subtracts the
    water vapor of the airway. PH2O comes from configuration. No fresh sourced cabin pressure, or a vapor pressure
    not below it, means no value: the reading is rejected and never compared with a PIO2 limit.
    """
    vapor, total = accepted.vapor_mmhg, pressure.pressure.value
    if vapor is None or not accepted.vapor_basis or not Decimal(0) <= vapor < total:
        raise InvalidValue("ppO2 to PIO2 needs a sourced water vapor pressure below the cabin pressure")
    with decimal_scope(arithmetic()):
        return ppo2 * (total - vapor) / total


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
    if accepted.conversion == "ppo2_to_pio2":
        return _convert_ppo2(converter, release, sensor_type, accepted, value, unit, canonical_unit, pressure, record)
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


def _convert_ppo2(
    converter: WireUnitConverter,
    release: ReleaseHandle,
    sensor_type: SensorTypeFacts,
    accepted: AcceptedUnit,
    value: Decimal,
    unit: str,
    canonical_unit: Unit,
    pressure: PressureContext | None,
    record: WireConversion | None,
) -> CanonicalReading:
    """The unit conversion (kPa to mmHg) is the converter's; the inspired-oxygen step needs cabin pressure."""
    context = _usable_pressure(pressure)
    ambient = converter.convert(
        WireQuantity(value, unit), indicator_code=sensor_type.id, release=release, to=canonical_unit, pressure=None
    )
    inspired = _pio2(require_finite(ambient.canonical.value), accepted, context)
    converted = ConvertedQuantity(
        ambient.original,
        Quantity(inspired, canonical_unit),
        "ppo2_to_pio2",
        ambient.mapping_version,
        context,
        ambient.rounding,
    )
    return CanonicalReading(converted, round_wire(require_finite(inspired)), record)

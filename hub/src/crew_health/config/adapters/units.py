"""SYSTEM 5.4 and D12: convert wire quantities from an immutable release pin."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, DecimalException, localcontext

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_sensor_catalog_schema import SensorCatalogJson
from crew_health.contracts_gen.schemas.config_unit_mapping_schema import Mapping, UnitMappingJson
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import (
    ConvertedQuantity,
    PressureContext,
    Quantity,
    Unit,
    UnitRegistry,
    WireQuantity,
)


def _invalid(code: ErrorCode = ErrorCode.INVALID_UNIT) -> ProblemError:
    return ProblemError(code, code.value)


def _mapping(release: ReleaseHandle, indicator: str, wire: str) -> tuple[str, Mapping]:
    try:
        document = UnitMappingJson.model_validate_json(release.document(ConfigName.UNIT_MAPPING).raw)
    except ValidationError as exc:
        raise _invalid(ErrorCode.INVALID_VALUE) from exc
    matches = [
        item for item in document.mappings if item.indicator_code == indicator and item.wire_unit == wire
    ]
    if len(matches) != 1:
        raise _invalid()
    return document.version, matches[0]


def _sensor_accepts(release: ReleaseHandle, indicator: str, mapping: Mapping) -> None:
    try:
        catalog = SensorCatalogJson.model_validate_json(release.document(ConfigName.SENSOR_CATALOG).raw)
    except ValidationError as exc:
        raise _invalid(ErrorCode.INVALID_VALUE) from exc
    for sensor in catalog.sensor_types:
        if sensor.id.root == indicator and not any(
            entry.unit == mapping.wire_unit and entry.conversion == mapping.conversion
            for entry in sensor.accepted_units
        ):
            raise _invalid()


class ReleaseWireUnitConverter:
    def __init__(self, registry: UnitRegistry) -> None:
        self.registry = registry

    def convert(
        self,
        value: WireQuantity,
        *,
        indicator_code: str,
        release: ReleaseHandle,
        to: Unit,
        pressure: PressureContext | None = None,
    ) -> ConvertedQuantity:
        version, mapping = _mapping(release, indicator_code, value.unit)
        _sensor_accepts(release, indicator_code, mapping)
        try:
            with localcontext() as context:
                # Bound Decimal work independently of the caller's ambient arithmetic context.
                context.prec = 64
                quantity = self._quantity(value, mapping, to, pressure)
                canonical = self.registry.convert(quantity, to)
                rounded = canonical.value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        except DecimalException as exc:
            raise _invalid(ErrorCode.INVALID_VALUE) from exc
        return ConvertedQuantity(
            value, Quantity(rounded, to), mapping.conversion, version, pressure, "half_even:4"
        )

    def _quantity(
        self, value: WireQuantity, mapping: Mapping, to: Unit, pressure: PressureContext | None
    ) -> Quantity:
        unit = self.registry.parse(mapping.ucum_unit)
        if mapping.conversion == "ppm_to_mmhg":
            if mapping.factor is not None or value.unit != "ppm" or unit.code != "[ppm]":
                raise _invalid()
            if not self.registry.commensurable(unit, to):
                return self._partial_pressure(value.value, pressure)
        if mapping.conversion == "scale":
            if mapping.factor is None:
                raise _invalid(ErrorCode.INVALID_VALUE)
            factor = Decimal(mapping.factor)
            if not factor.is_finite() or factor <= 0:
                raise _invalid(ErrorCode.INVALID_VALUE)
            return Quantity(value.value * factor, unit)
        if mapping.factor is not None:
            raise _invalid(ErrorCode.INVALID_VALUE)
        return Quantity(value.value, unit)

    def _partial_pressure(self, ppm: Decimal, pressure: PressureContext | None) -> Quantity:
        if (
            pressure is None
            or pressure.pressure.value <= 0
            or pressure.basis not in {"measured", "assumed"}
            or not pressure.source_ref.strip()
        ):
            raise _invalid(ErrorCode.INVALID_VALUE)
        unit = self.registry.parse("mm[Hg]")
        cabin = self.registry.convert(pressure.pressure, unit)
        if cabin.value <= 0:
            raise _invalid(ErrorCode.INVALID_VALUE)
        return Quantity(ppm / Decimal("1000000") * cabin.value, unit)

"""Quantities with units, and the physical constants the cabin model needs.

Constants here are physics, not mission content: each names its standard. Mission content
(limits, rates, volumes, device specifications) lives in the configuration documents.
"""

import math
from dataclasses import dataclass

from crew_health_sim_habitat.errors import NonFiniteValueError

# CODATA 2018 molar gas constant, J/(mol K).
GAS_CONSTANT = 8.314462618
# 0 degC in kelvin (SI definition).
CELSIUS_OFFSET = 273.15
# Standard atmosphere: 101.325 kPa = 760 mmHg (definition of the mmHg used by NASA-SOURCES.md section 2).
MMHG_PER_KPA = 760.0 / 101.325
# International avoirdupois pound and inch: 1 psi = 6.894757293168 kPa.
KPA_PER_PSI = 6.894757293168
# Molar masses in kg/mol (IUPAC standard atomic weights).
MOLAR_MASS_CO2 = 0.04401
MOLAR_MASS_O2 = 0.031998


@dataclass(frozen=True, slots=True)
class Quantity:
    """A finite value with its unit string, exactly as it travels on the wire."""

    value: float
    unit: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise NonFiniteValueError(f"non-finite value for unit {self.unit!r}")
        if not self.unit:
            raise NonFiniteValueError("a quantity needs a unit")


def quantize(value: float, resolution: float) -> float:
    """Round to the device resolution, the way a sensor's output register does."""
    if resolution <= 0:
        return value
    steps = round(value / resolution)
    # Round the product again so 0.1 steps do not print as 0.30000000000000004.
    decimals = max(0, -math.floor(math.log10(resolution)) + 1)
    return round(steps * resolution, decimals)


def saturation_vapor_pressure_kpa(temperature_c: float) -> float:
    """Magnus formula over water (WMO-No. 8, 2018, Annex 4.B), in kPa."""
    return 0.6112 * math.exp(17.62 * temperature_c / (243.12 + temperature_c))

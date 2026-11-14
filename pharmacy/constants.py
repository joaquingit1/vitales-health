"""Constants of nature and unit conversions. Nothing else may be a code constant.

Source: CODATA 2022 recommended values of the fundamental physical constants
(E. Tiesinga, P. J. Mohr, D. B. Newell, B. N. Taylor, and the NIST team, CODATA
Recommended Values of the Fundamental Physical Constants: 2022, published by NIST,
https://physics.nist.gov/cuu/Constants/). The SI defining constants (Avogadro,
Boltzmann, elementary charge, Planck) are exact since the 2019 SI revision; the
molar gas constant is their exact product. The Hartree energy and the Bohr radius
are measured CODATA 2022 values (their uncertainties are far below anything this
engine resolves).

Every product, package, zone, prior, threshold or method parameter belongs in the
versioned JSON configuration under config/pharmacy, never here (docs/ENGINE-SPEC.md
section 1.3).
"""

from typing import Final

# --- SI defining constants (exact) ---------------------------------------------
AVOGADRO_PER_MOL: Final[float] = 6.02214076e23
"""Avogadro constant N_A in mol^-1 (exact)."""

BOLTZMANN_J_PER_K: Final[float] = 1.380649e-23
"""Boltzmann constant k_B in J K^-1 (exact)."""

ELEMENTARY_CHARGE_C: Final[float] = 1.602176634e-19
"""Elementary charge e in C (exact)."""

PLANCK_J_S: Final[float] = 6.62607015e-34
"""Planck constant h in J s (exact)."""

GAS_CONSTANT_J_PER_MOL_K: Final[float] = AVOGADRO_PER_MOL * BOLTZMANN_J_PER_K
"""Molar gas constant R = N_A k_B in J mol^-1 K^-1 (exact, 8.31446261815324)."""

# --- CODATA 2022 measured constants ----------------------------------------------
HARTREE_ENERGY_J: Final[float] = 4.3597447222060e-18
"""Hartree energy E_h in J (CODATA 2022)."""

BOHR_RADIUS_M: Final[float] = 5.29177210544e-11
"""Bohr radius a_0 in m (CODATA 2022)."""

# --- Derived conversions (exact arithmetic on the constants above) -----------------
HARTREE_TO_J_PER_MOL: Final[float] = HARTREE_ENERGY_J * AVOGADRO_PER_MOL
"""One hartree per molecule expressed in J mol^-1."""

ANGSTROM_M: Final[float] = 1.0e-10
"""One angstrom in m (definition)."""

ANGSTROM_TO_BOHR: Final[float] = ANGSTROM_M / BOHR_RADIUS_M
"""Multiply a length in angstrom by this to get bohr."""

ELECTRONVOLT_J: Final[float] = ELEMENTARY_CHARGE_C
"""One electronvolt in J (1 eV = e x 1 V)."""

G_VALUE_MOLECULES_PER_100EV_TO_MOL_PER_J: Final[float] = 1.0 / (100.0 * ELECTRONVOLT_J * AVOGADRO_PER_MOL)
"""Radiation chemical yield: 1 molecule per 100 eV = 1.0364e-7 mol J^-1 (matches the
0.10364 umol/J conversion quoted in research source MDS63)."""

GRAY_J_PER_KG: Final[float] = 1.0
"""Absorbed dose: 1 Gy = 1 J kg^-1 (definition)."""

# --- Unit conventions (definitions, not measurements) --------------------------------
ZERO_CELSIUS_K: Final[float] = 273.15
"""Kelvin value of 0 degrees Celsius (definition of the Celsius scale)."""

THERMOCHEMICAL_CALORIE_J: Final[float] = 4.184
"""Thermochemical calorie in J (definition; research vector TV-KIN-7)."""

SECONDS_PER_MINUTE: Final[float] = 60.0
SECONDS_PER_HOUR: Final[float] = 3600.0
SECONDS_PER_DAY: Final[float] = 86400.0
DAYS_PER_JULIAN_YEAR: Final[float] = 365.25
SECONDS_PER_JULIAN_YEAR: Final[float] = DAYS_PER_JULIAN_YEAR * SECONDS_PER_DAY
SECONDS_PER_MONTH: Final[float] = SECONDS_PER_JULIAN_YEAR / 12.0
"""Mean month of the Julian year (30.4375 days). Used only to convert published rates
given per month (research source MDS61); the convention is fixed here so every
conversion in the engine and its tests agrees."""

LUX_SECONDS_PER_LUX_HOUR: Final[float] = SECONDS_PER_HOUR
"""Light dose: 1 lux h = 3600 lux s (ICH Q1B expresses exposure in lux h and W h m^-2)."""

GRAMS_PER_KILOGRAM: Final[float] = 1000.0
"""SI prefix definition."""

J_PER_KJ: Final[float] = 1000.0
"""SI prefix definition."""

PERCENT: Final[float] = 100.0
"""Percent scale factor (relative humidity and potency are carried in percent)."""

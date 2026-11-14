"""Shared mpmath context and constants of the oracle (docs/ENGINE-SPEC.md 1.7 and 15.2).

The oracle is written from ENGINE-SPEC.md alone. It imports only ``pharmacy.types``,
mpmath and the standard library, and restates every constant it needs from its primary
definition (CODATA 2022 and the SI), never from ``pharmacy.constants``.

A private ``MPContext`` at 50 significant digits is used so the oracle never changes the
global ``mpmath.mp`` state other code may rely on.
"""

from fractions import Fraction
from typing import Any, Final

from mpmath.ctx_mp import MPContext

type Mpf = Any
"""mpmath numbers are untyped; the alias documents intent in signatures."""

DIGITS: Final[int] = 50
MP: Final[Any] = MPContext()
MP.dps = DIGITS

# --- SI defining constants (exact, 2019 SI) -------------------------------------------------
AVOGADRO_PER_MOL_TEXT: Final[str] = "6.02214076e23"
BOLTZMANN_J_PER_K_TEXT: Final[str] = "1.380649e-23"

R_EXACT: Final[Fraction] = Fraction(AVOGADRO_PER_MOL_TEXT) * Fraction(BOLTZMANN_J_PER_K_TEXT)
"""Molar gas constant R = N_A k_B as an exact rational: 8.31446261815324 J/(mol K)."""
R_MP: Final[Mpf] = MP.mpf(R_EXACT.numerator) / R_EXACT.denominator

R_FLOAT: Final[float] = float(R_MP)

# --- Unit definitions (exact) -------------------------------------------------------------------
SECONDS_PER_HOUR: Final[int] = 3600
SECONDS_PER_DAY: Final[int] = 86400
SECONDS_PER_MONTH_MP: Final[Mpf] = MP.mpf("365.25") * SECONDS_PER_DAY / 12
"""Mean month of the Julian year (spec 1.1)."""
ZERO_CELSIUS_K_TEXT: Final[str] = "273.15"
PERCENT: Final[int] = 100

# --- Agreement rule (spec 1.7) -------------------------------------------------------------------
RELATIVE_TOLERANCE: Final[float] = 1e-9
ABSOLUTE_TOLERANCE: Final[float] = 1e-12


def mpf(value: float | int | str) -> Mpf:
    """Exact conversion of a float (or exact decimal text) to the oracle's precision."""
    return MP.mpf(value)


def to_float(value: Mpf) -> float:
    """Round an oracle number to the nearest float."""
    return float(MP.mpf(value))


def agrees(
    engine: float, oracle: Mpf, rel: float = RELATIVE_TOLERANCE, abs_tol: float = ABSOLUTE_TOLERANCE
) -> bool:
    """Spec 1.7: |engine - oracle| <= max(rel |oracle|, abs) evaluated at oracle precision.
    Infinities agree only with the same infinity."""
    exact = MP.mpf(oracle)
    if MP.isinf(exact) or MP.isinf(MP.mpf(engine)):
        return bool(MP.mpf(engine) == exact)
    diff = abs(MP.mpf(engine) - exact)
    return bool(diff <= max(MP.mpf(rel) * abs(exact), MP.mpf(abs_tol)))


def relative_difference(engine: float, oracle: Mpf) -> float:
    """|engine - oracle| / |oracle| (absolute difference when the oracle is zero)."""
    exact = MP.mpf(oracle)
    diff = abs(MP.mpf(engine) - exact)
    return float(diff / abs(exact)) if exact != 0 else float(diff)

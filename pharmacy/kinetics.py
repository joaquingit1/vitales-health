"""Kinetics: rate constants, integrated rate, closed-form conversion, pathway composition.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 5 (and 6 for the radiation term, 8 for
regimes and the validated range).

Separable rate law d(alpha)/dt = k(T, RH) f(alpha). Over piecewise-constant segments the
integrated rate Theta = sum_i k_i tau_i is exact and order independent, and
alpha = G(Theta) has a closed form (or a bracketed inverse) for every model kind.
Remaining drug C = C0 (1 - alpha) in percent of label claim; for racemizing ingredients the
active fraction rho multiplies it (spec 5.7).
"""

import dataclasses
import math
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from functools import cache
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp
from scipy.special import expi, gammainc

from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, PERCENT, SECONDS_PER_HOUR
from pharmacy.errors import ComputationError, ConvergenceError, InputError, ModelDomainError
from pharmacy.types import (
    EngineSettingsDoc,
    LotExposure,
    MicroclimateTrace,
    NumericsSettings,
    PackageDoc,
    Pathway,
    Placard,
    RateLawKind,
    StabilityModel,
    StorageZoneDoc,
)

type FloatArray = NDArray[np.float64]
type BoolArray = NDArray[np.bool_]

# --- Structural constants fixed by the specification (not tunable configuration) ---------
_MAX_INVERSE_ITERATIONS: Final[int] = 200
"""Iteration cap of the D2 and D4 safeguarded Newton inverse (spec 5.3)."""

_SERIES_SWITCH_X: Final[float] = -5.0
"""J uses its power series for x >= -5 and the exponential-integral form below (spec 5.4)."""

_ODE_SINGULAR_START_ALPHA: Final[float] = 1e-9
"""Start of the ODE reference for kinds whose f is singular or zero at alpha = 0 (spec 5.9)."""

# --- Numerical-method choices the spec leaves open (docs/requests/B1.md R-03) ---------------
_QUADRATURE_SPAN: Final[float] = 1.0
"""J uses Gauss-Legendre quadrature instead of the spec's series (x >= -5) or Ei difference
(x < -5) when the exponent of the integrand varies by at most this much,
|x| (1 - e^-delta) <= 1, and the segment is short, delta <= _QUADRATURE_DELTA. There the
alternating series and the Ei difference both cancel for x < 0 (the series loses up to
e^|x|, the Ei difference up to 1/(|x| delta)); the quadrature has no cancellation for
either sign of x (request R-03)."""

_QUADRATURE_DELTA: Final[float] = 0.1
"""Largest delta = Delta / tau_m for the quadrature branch (request R-03)."""

_GAUSS_LEGENDRE_NODES: Final[int] = 16
"""Gauss-Legendre nodes of the short-span J quadrature at its widest. With span <= 1 and
delta <= 0.1 the integrand is analytic in a Bernstein ellipse of parameter rho >= 30 with
modulus below e^15, so the 16-node error bound is below 1e-40 relative (request R-03)."""

_GAUSS_LEGENDRE_LADDER: Final[tuple[tuple[float, int], ...]] = (
    (1e-5, 2),
    (1e-3, 3),
    (5e-3, 4),
    (0.03, 6),
    (0.1, 8),
)
"""Fewer nodes when m = max(span, delta) is small: (largest m, nodes). Each rung keeps the
quadrature error at the rounding level of float64 (measured against mpmath at 40 digits
over both signs of x and delta from 1e-6 m to m: worst 9.4e-16 relative, request R-03);
the 16-node rule covers the rest of the branch. Purely a speed choice: sealed-like packages
with tau_m of years have m near 1e-5."""

_KINDS: Final[tuple[RateLawKind, ...]] = (
    "F0",
    "F1",
    "F2",
    "Fn",
    "R2",
    "R3",
    "A2",
    "A3",
    "A4",
    "An",
    "B1",
    "D1",
    "D2",
    "D3",
    "D4",
)
_AVRAMI_FIXED: Final[Mapping[str, float]] = {"A2": 2.0, "A3": 3.0, "A4": 4.0}
_COMPLETES_AT_ONE: Final[frozenset[str]] = frozenset({"F0", "R2", "R3", "D1", "D2", "D3"})


# =========================================================================================
# Scalar building blocks (oracle-checked to 1e-9 relative, spec 15.2)
# =========================================================================================


def _finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ModelDomainError(f"{name} must be finite, got {value!r}")
    return float(value)


def _exp_checked(exponent: float, what: str) -> float:
    if exponent > math.log(np.finfo(np.float64).max):
        raise ComputationError(f"{what}: exponent {exponent!r} overflows float64")
    return math.exp(exponent)


def rate_constant(
    ln_k_ref_per_s: float,
    ea_j_per_mol: float,
    b_per_percent_rh: float,
    temperature_k: float,
    rh_percent: float,
    reference_temperature_k: float,
    reference_rh_percent: float,
    gas_constant_j_per_mol_k: float = GAS_CONSTANT_J_PER_MOL_K,
) -> float:
    """Humidity-corrected Arrhenius rate constant in 1/s (spec 5.1):

        k = exp( ln_k_ref - (Ea/R)(1/T - 1/T_ref) + B (RH - RH_ref) )

    evaluated as one exponential of the summed exponent. Raises ``ModelDomainError`` if
    T <= 0 or T_ref <= 0."""
    for name, value in (
        ("ln_k_ref_per_s", ln_k_ref_per_s),
        ("ea_j_per_mol", ea_j_per_mol),
        ("b_per_percent_rh", b_per_percent_rh),
        ("temperature_k", temperature_k),
        ("rh_percent", rh_percent),
        ("reference_temperature_k", reference_temperature_k),
        ("reference_rh_percent", reference_rh_percent),
        ("gas_constant_j_per_mol_k", gas_constant_j_per_mol_k),
    ):
        _finite(name, value)
    if temperature_k <= 0.0 or reference_temperature_k <= 0.0:
        raise ModelDomainError(
            f"temperatures must be positive: T={temperature_k!r}, T_ref={reference_temperature_k!r}"
        )
    if gas_constant_j_per_mol_k <= 0.0:
        raise ModelDomainError("the gas constant must be positive")
    # 1/T - 1/T_ref written as (T_ref - T)/(T T_ref) so T close to T_ref does not cancel.
    inverse_gap = (reference_temperature_k - temperature_k) / (temperature_k * reference_temperature_k)
    exponent = (
        ln_k_ref_per_s
        - (ea_j_per_mol / gas_constant_j_per_mol_k) * inverse_gap
        + b_per_percent_rh * (rh_percent - reference_rh_percent)
    )
    return _exp_checked(exponent, "rate_constant")


def integrated_rate(
    ln_k_ref_per_s: float,
    ea_j_per_mol: float,
    b_per_percent_rh: float,
    temperatures_k: Sequence[float],
    rh_percent: Sequence[float],
    durations_s: Sequence[float],
    reference_temperature_k: float,
    reference_rh_percent: float,
    gas_constant_j_per_mol_k: float = GAS_CONSTANT_J_PER_MOL_K,
) -> float:
    """Theta = sum_i k(T_i, RH_i) tau_i (dimensionless) over piecewise-constant segments
    with RH as given (no package dynamics), spec 5.2. Order independent up to floating-point
    summation. Empty input returns 0.0. Raises ``InputError`` on mismatched lengths or a
    negative duration."""
    if not len(temperatures_k) == len(rh_percent) == len(durations_s):
        raise InputError(
            f"mismatched lengths: {len(temperatures_k)} temperatures, {len(rh_percent)} RH values, "
            f"{len(durations_s)} durations"
        )
    terms: list[float] = []
    for temperature, rh, duration in zip(temperatures_k, rh_percent, durations_s, strict=True):
        if not math.isfinite(duration) or duration < 0.0:
            raise InputError(f"durations must be finite and non-negative, got {duration!r}")
        k = rate_constant(
            ln_k_ref_per_s,
            ea_j_per_mol,
            b_per_percent_rh,
            temperature,
            rh,
            reference_temperature_k,
            reference_rh_percent,
            gas_constant_j_per_mol_k,
        )
        terms.append(k * duration)
    # Correctly rounded sum: the result does not depend on segment order at all.
    total = math.fsum(terms)
    if not math.isfinite(total):
        raise ComputationError("integrated rate overflows float64")
    return total


# --- Model kinds -----------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _Kind:
    """A validated rate-law kind with its single shape parameter (n, m or alpha0)."""

    kind: RateLawKind
    parameter: float


def _resolve_kind(kind: str, order_n: float | None, alpha0: float | None, avrami_n: float | None) -> _Kind:
    if kind not in _KINDS:
        raise ModelDomainError(f"unknown rate-law kind {kind!r}")
    resolved: RateLawKind = _KINDS[_KINDS.index(kind)]
    if resolved == "Fn":
        if order_n is None or not math.isfinite(order_n) or order_n <= 0.0 or order_n == 1.0:
            raise ModelDomainError(f"kind Fn needs order_n > 0 and != 1, got {order_n!r}")
        return _Kind(resolved, float(order_n))
    if resolved == "An":
        if avrami_n is None or not math.isfinite(avrami_n) or avrami_n <= 0.0:
            raise ModelDomainError(f"kind An needs avrami_n > 0, got {avrami_n!r}")
        return _Kind(resolved, float(avrami_n))
    if resolved in _AVRAMI_FIXED:
        return _Kind(resolved, _AVRAMI_FIXED[resolved])
    if resolved == "B1":
        if alpha0 is None or not math.isfinite(alpha0) or not 0.0 < alpha0 < 1.0:
            raise ModelDomainError(f"kind B1 needs alpha0 in (0, 1), got {alpha0!r}")
        return _Kind(resolved, float(alpha0))
    return _Kind(resolved, 0.0)


def _is_avrami(kind: RateLawKind) -> bool:
    return kind in _AVRAMI_FIXED or kind == "An"


def _completion(spec: _Kind) -> float:
    if spec.kind in _COMPLETES_AT_ONE:
        return 1.0
    if spec.kind == "Fn" and spec.parameter < 1.0:
        return 1.0 / (1.0 - spec.parameter)
    if spec.kind == "D4":
        return 1.0 / 3.0
    return math.inf


def completion_theta(
    kind: RateLawKind,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> float:
    """Theta at which alpha reaches 1, or ``math.inf`` for asymptotic kinds."""
    return _completion(_resolve_kind(kind, order_n, alpha0, avrami_n))


def _numerics_values(numerics: NumericsSettings | None, kind: str) -> tuple[float, float, int]:
    if numerics is None:
        # docs/requests/B1.md R-01: the D2 and D4 inverses need the configured tolerances.
        raise ModelDomainError(f"kind {kind} needs the numerics settings (engine-settings.json numerics)")
    return (
        float(numerics["inverse_absolute_tolerance"]["value"]),
        float(numerics["small_alpha_series_threshold"]["value"]),
        int(numerics["series_max_terms"]["value"]),
    )


def _small_alpha_series(kind: RateLawKind, x: FloatArray, max_terms: int) -> FloatArray:
    """g(alpha) by its power series for small alpha (spec 5.3): D2 sum_{n>=2} a^n/(n(n-1));
    D4 a^2/9 + 4 a^3/81 + ... with c_{k+1} = c_k (k - 2/3)/(k + 1). All terms are positive,
    so the sum has no cancellation; it stops when a term no longer changes the sum."""
    eps = float(np.finfo(np.float64).eps)
    if kind == "D2":
        power = x * x
        total = power / 2.0
        for n in range(3, max_terms + 3):
            power = power * x
            term = power / (n * (n - 1))
            total = total + term
            if bool(np.all(term <= eps * total)):
                return total
    else:
        coefficient = 1.0 / 9.0
        power = x * x
        total = coefficient * power
        for k in range(2, max_terms + 2):
            coefficient = coefficient * (k - 2.0 / 3.0) / (k + 1.0)
            power = power * x
            term = coefficient * power
            total = total + term
            if bool(np.all(term <= eps * total)):
                return total
    raise ConvergenceError(f"the small-alpha series of {kind} did not converge in {max_terms} terms")


def _g_closed(kind: RateLawKind, x: FloatArray) -> FloatArray:
    """Cancellation-free closed forms of g for D2 and D4 (used above the series threshold
    and by ``theta_from_conversion``): D2 g = P(2, L) = 1 - e^-L (1 + L) with L = -ln(1 - a)
    (regularized incomplete gamma); D4 g = (1 - v)^2 (1 + 2 v) / 3 with v = (1 - a)^(1/3)."""
    log_remaining = np.log1p(-x)
    if kind == "D2":
        return np.asarray(gammainc(2.0, -log_remaining), dtype=np.float64)
    one_minus_v = -np.expm1(log_remaining / 3.0)
    v = 1.0 - one_minus_v
    return one_minus_v * one_minus_v * (1.0 + 2.0 * v) / 3.0


def _g_prime(kind: RateLawKind, x: FloatArray) -> FloatArray:
    log_remaining = np.log1p(-x)
    if kind == "D2":
        return -log_remaining
    return (2.0 / 3.0) * np.expm1(-log_remaining / 3.0)


def _newton_inverse(kind: RateLawKind, theta: FloatArray, numerics: NumericsSettings | None) -> FloatArray:
    """Safeguarded Newton inverse of g for D2 and D4 (spec 5.3)."""
    tolerance, threshold, max_terms = _numerics_values(numerics, kind)
    complete = 1.0 if kind == "D2" else 1.0 / 3.0
    out = np.zeros_like(theta)
    out[theta >= complete] = 1.0
    active = (theta > 0.0) & (theta < complete)
    if not bool(np.any(active)):
        return out
    target = theta[active]
    start = np.sqrt(2.0 * target) if kind == "D2" else 3.0 * np.sqrt(target)
    x = np.where(start < threshold, start, 0.5)
    lo = np.zeros_like(x)
    hi = np.ones_like(x)
    done = np.zeros(x.shape, dtype=np.bool_)
    with np.errstate(divide="ignore", invalid="ignore"):
        for _ in range(_MAX_INVERSE_ITERATIONS):
            small = x < threshold
            g = np.empty_like(x)
            if bool(np.any(small)):
                g[small] = _small_alpha_series(kind, x[small], max_terms)
            if bool(np.any(~small)):
                g[~small] = _g_closed(kind, x[~small])
            h = g - target
            lo = np.where(~done & (h <= 0.0), x, lo)
            hi = np.where(~done & (h > 0.0), x, hi)
            slope = _g_prime(kind, x)
            x_newton = x - h / slope
            # At alpha = 1 the slope g' is infinite, so a Newton step from there never moves
            # and would pass the step test without converging: bisect instead.
            inside = np.isfinite(slope) & (x_newton >= lo) & (x_newton <= hi)
            x_new = np.where(inside, x_newton, 0.5 * (lo + hi))
            step = np.abs(x_new - x)
            x = np.where(done, x, x_new)
            done = done | (step < tolerance)
            if bool(np.all(done)):
                out[active] = x
                return out
    raise ConvergenceError(f"the {kind} inverse did not converge in {_MAX_INVERSE_ITERATIONS} iterations")


def _conversion_array(spec: _Kind, theta: FloatArray, numerics: NumericsSettings | None) -> FloatArray:
    kind = spec.kind
    if kind == "F0":
        return np.minimum(theta, 1.0)
    if kind == "F1":
        return -np.expm1(-theta)
    if kind == "F2":
        return theta / (1.0 + theta)
    if kind == "Fn":
        n = spec.parameter
        if n > 1.0:
            return -np.expm1(np.log1p((n - 1.0) * theta) / (1.0 - n))
        out = np.ones_like(theta)
        running = theta < 1.0 / (1.0 - n)
        with np.errstate(divide="ignore"):
            out[running] = -np.expm1(np.log1p((n - 1.0) * theta[running]) / (1.0 - n))
        return out
    if kind == "R2":
        clipped = np.minimum(theta, 1.0)
        return clipped * (2.0 - clipped)
    if kind == "R3":
        clipped = np.minimum(theta, 1.0)
        return np.asarray(clipped * (3.0 - 3.0 * clipped + clipped * clipped), dtype=np.float64)
    if _is_avrami(kind):
        with np.errstate(over="ignore"):
            power = np.power(theta, spec.parameter)
        return -np.expm1(-power)
    if kind == "B1":
        a0 = spec.parameter
        return a0 * (-np.expm1(-theta)) / (a0 + (1.0 - a0) * np.exp(-theta))
    if kind == "D1":
        return np.sqrt(np.minimum(theta, 1.0))
    if kind == "D3":
        s = np.sqrt(np.minimum(theta, 1.0))
        return np.asarray(s * (3.0 - 3.0 * s + s * s), dtype=np.float64)
    return _newton_inverse(kind, theta, numerics)


def _theta_array(value: FloatArray | float) -> FloatArray:
    theta = np.asarray(value, dtype=np.float64)
    if theta.size and not bool(np.all(np.isfinite(theta))):
        raise ModelDomainError("Theta must be finite")
    if theta.size and bool(np.any(theta < 0.0)):
        raise ModelDomainError("Theta must be non-negative")
    return theta


def conversion_from_theta(
    kind: RateLawKind,
    theta: float,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
    numerics: NumericsSettings | None = None,
) -> float:
    """alpha = G(Theta) for a model kind (spec 5.3, table 5.3.1). Theta >= 0.

    Closed forms: F0 min(Theta, 1); F1 -expm1(-Theta); F2 Theta/(1+Theta);
    Fn 1 - (1 + (n-1)Theta)^(1/(1-n)) via -expm1(log1p((n-1)Theta)/(1-n)), complete at
    Theta >= 1/(1-n) when n < 1; R2 1 - (1 - Theta)^2 for Theta <= 1; R3 1 - (1 - Theta)^3
    for Theta <= 1; A2, A3, A4, An -expm1(-Theta^m) (m = 2, 3, 4, avrami_n); B1 relative
    form with seed alpha0; D1 sqrt(Theta) for Theta <= 1; D3 Jander via s = sqrt(Theta);
    D2 and D4 by safeguarded Newton on g with a series start for alpha <= the small-alpha
    threshold. Raises ``ModelDomainError`` for missing or invalid kind parameters or
    Theta < 0.
    """
    array = conversion_from_theta_array(
        kind,
        np.array([theta], dtype=np.float64),
        order_n=order_n,
        alpha0=alpha0,
        avrami_n=avrami_n,
        numerics=numerics,
    )
    return float(array[0])


def conversion_from_theta_array(
    kind: RateLawKind,
    theta: FloatArray,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
    numerics: NumericsSettings | None = None,
) -> FloatArray:
    """Vectorized ``conversion_from_theta``; element-wise identical to the scalar form."""
    spec = _resolve_kind(kind, order_n, alpha0, avrami_n)
    values = _theta_array(theta)
    return np.asarray(_conversion_array(spec, values, numerics), dtype=np.float64)


def _check_alpha(spec: _Kind, alpha: float) -> float:
    if not math.isfinite(alpha) or alpha < 0.0 or alpha > 1.0:
        raise ModelDomainError(f"alpha must lie in [0, 1], got {alpha!r}")
    if alpha == 1.0 and math.isinf(_completion(spec)):
        raise ModelDomainError(f"kind {spec.kind} never completes: alpha = 1 has no finite Theta")
    return float(alpha)


def _g_scalar(spec: _Kind, alpha: float) -> float:
    kind = spec.kind
    if kind in ("F0",):
        return alpha
    if kind == "F1":
        return -math.log1p(-alpha)
    if kind == "F2":
        return alpha / (1.0 - alpha)
    if kind == "Fn":
        n = spec.parameter
        if alpha == 1.0:
            return 1.0 / (1.0 - n)
        return math.expm1((1.0 - n) * math.log1p(-alpha)) / (n - 1.0)
    if kind == "R2":
        return alpha / (1.0 + math.sqrt(1.0 - alpha))
    if kind == "R3":
        if alpha == 1.0:
            return 1.0
        return -math.expm1(math.log1p(-alpha) / 3.0)
    if _is_avrami(kind):
        return float((-math.log1p(-alpha)) ** (1.0 / spec.parameter))
    if kind == "B1":
        a0 = spec.parameter
        return math.log1p((1.0 - a0) * alpha / a0) - math.log1p(-alpha)
    if kind == "D1":
        return alpha * alpha
    if kind == "D3":
        if alpha == 1.0:
            return 1.0
        u = -math.expm1(math.log1p(-alpha) / 3.0)
        return u * u
    if alpha == 1.0:
        return 1.0 if kind == "D2" else 1.0 / 3.0
    return float(_g_closed(kind, np.array([alpha], dtype=np.float64))[0])


def theta_from_conversion(
    kind: RateLawKind,
    alpha: float,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> float:
    """Theta = g(alpha), the integral form (spec 5.3). 0 <= alpha < 1 (alpha = 1 allowed
    for kinds that complete at finite Theta). Used by the assay re-anchor (spec 10.4) and
    by forecasts (spec 11.4)."""
    spec = _resolve_kind(kind, order_n, alpha0, avrami_n)
    return _g_scalar(spec, _check_alpha(spec, alpha))


def _f_scalar(spec: _Kind, alpha: float) -> float:
    kind = spec.kind
    remaining = 1.0 - alpha
    if kind == "F0":
        return 1.0
    if kind == "F1":
        return remaining
    if kind == "F2":
        return remaining * remaining
    if kind == "Fn":
        return float(remaining**spec.parameter)
    if kind == "R2":
        return 2.0 * math.sqrt(remaining)
    if kind == "R3":
        return float(3.0 * remaining ** (2.0 / 3.0))
    if _is_avrami(kind):
        m = spec.parameter
        if alpha == 1.0:
            return 0.0
        exponent = (m - 1.0) / m
        if alpha == 0.0:
            if exponent < 0.0:
                raise ModelDomainError(f"f of kind {kind} with m < 1 is singular at alpha = 0")
            return m if exponent == 0.0 else 0.0
        return float(m * remaining * (-math.log1p(-alpha)) ** exponent)
    if kind == "B1":
        a0 = spec.parameter
        return (a0 + (1.0 - a0) * alpha) * remaining
    # Diffusion kinds: f is singular at alpha = 0.
    if alpha == 0.0:
        raise ModelDomainError(f"f of kind {kind} is singular at alpha = 0")
    if kind == "D1":
        return 1.0 / (2.0 * alpha)
    if kind == "D2":
        return 0.0 if alpha == 1.0 else 1.0 / (-math.log1p(-alpha))
    if kind == "D3":
        if alpha == 1.0:
            return 0.0
        u = -math.expm1(math.log1p(-alpha) / 3.0)
        return float(1.5 * remaining ** (2.0 / 3.0) / u)
    if alpha == 1.0:
        return 0.0
    return 1.5 / math.expm1(-math.log1p(-alpha) / 3.0)


def rate_function(
    kind: RateLawKind,
    alpha: float,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> float:
    """f(alpha) of d(alpha)/dt = k f(alpha) (spec 5.3), consistent with g' = 1/f. Used by
    the ODE reference."""
    spec = _resolve_kind(kind, order_n, alpha0, avrami_n)
    if not math.isfinite(alpha) or alpha < 0.0 or alpha > 1.0:
        raise ModelDomainError(f"alpha must lie in [0, 1], got {alpha!r}")
    return _f_scalar(spec, float(alpha))


# --- Humidity integral -----------------------------------------------------------------------


@cache
def _gauss_legendre(count: int) -> tuple[FloatArray, FloatArray]:
    nodes, weights = np.polynomial.legendre.leggauss(count)
    return np.asarray(nodes, dtype=np.float64), np.asarray(weights, dtype=np.float64)


def _humidity_integral_array(
    x: FloatArray, duration: FloatArray, tau_m: FloatArray, rel_tol: float, max_terms: int
) -> FloatArray:
    """Vectorized J(x, D, tau_m) of spec 5.4 over broadcast arrays (D >= 0, tau_m >= 0,
    tau_m may be inf). Element-wise identical to ``humidity_integral``."""
    x_b, d_b, tau_b = np.broadcast_arrays(
        np.asarray(x, dtype=np.float64),
        np.asarray(duration, dtype=np.float64),
        np.asarray(tau_m, dtype=np.float64),
    )
    out = np.array(d_b, dtype=np.float64, copy=True)  # tau_m = 0 (kind none) and x = 0 give D
    sealed = np.isinf(tau_b)
    if bool(np.any(sealed)):
        with np.errstate(over="ignore"):  # an overflow is reported by the caller's finiteness check
            out[sealed] = d_b[sealed] * np.exp(x_b[sealed])
    exchange = ~sealed & (tau_b > 0.0) & (d_b > 0.0) & (x_b != 0.0)
    if not bool(np.any(exchange)):
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        delta = np.where(exchange, d_b / np.where(exchange, tau_b, 1.0), 0.0)
    span = np.abs(x_b) * -np.expm1(-delta)
    quad = exchange & (delta <= _QUADRATURE_DELTA) & (span <= _QUADRATURE_SPAN)
    if bool(np.any(quad)):
        measure = np.maximum(span, delta)
        remaining = quad.copy()
        for limit, nodes in (*_GAUSS_LEGENDRE_LADDER, (math.inf, _GAUSS_LEGENDRE_NODES)):
            rung = remaining & (measure <= limit)
            if bool(np.any(rung)):
                out[rung] = _j_quadrature(x_b[rung], d_b[rung], tau_b[rung], nodes)
                remaining = remaining & ~rung
    series = exchange & ~quad & (x_b >= _SERIES_SWITCH_X)
    if bool(np.any(series)):
        out[series] = _j_series(x_b[series], d_b[series], tau_b[series], rel_tol, max_terms)
    ei = exchange & ~quad & (x_b < _SERIES_SWITCH_X)
    if bool(np.any(ei)):
        out[ei] = tau_b[ei] * (expi(x_b[ei]) - _ei_of_decayed(x_b[ei], delta[ei]))
    return out


def _ei_of_decayed(x: FloatArray, delta: FloatArray) -> FloatArray:
    """Ei(x e^-delta) for x < 0. When x e^-delta is below the smallest normal float the
    leading terms of Ei(y) = gamma + ln|y| + y + ... are exact in float64, and ln|y| is
    formed as ln|x| - delta so a long segment never underflows to Ei(0) = -inf."""
    log_abs_y = np.log(-x) - delta
    tiny = log_abs_y < math.log(float(np.finfo(np.float64).tiny))
    y = x * np.exp(-delta)
    return np.where(tiny, np.euler_gamma + log_abs_y, expi(np.where(tiny, -1.0, y)))


def _j_series(x: FloatArray, d: FloatArray, tau: FloatArray, rel_tol: float, max_terms: int) -> FloatArray:
    delta = d / tau
    total = d.copy()
    power = np.ones_like(x)  # x^n / n!
    converged = np.zeros(x.shape, dtype=np.bool_)
    magnitude = np.abs(x)
    for n in range(1, max_terms + 1):
        power = power * x / n
        term = tau * power * (-np.expm1(-n * delta)) / n
        total = np.where(converged, total, total + term)
        converged = converged | ((n > magnitude) & (np.abs(term) <= rel_tol * np.abs(total)))
        if bool(np.all(converged)):
            return total
    raise ConvergenceError(f"humidity integral series did not converge in {max_terms} terms")


def _j_quadrature(x: FloatArray, d: FloatArray, tau: FloatArray, count: int) -> FloatArray:
    nodes, weights = _gauss_legendre(count)
    half = 0.5 * d
    total = np.zeros_like(x)
    for node, weight in zip(nodes, weights, strict=True):
        t = half * (node + 1.0)
        total = total + weight * np.exp(x * np.exp(-t / tau))
    return half * total


def humidity_integral(
    x: float,
    duration_s: float,
    tau_m_s: float,
    *,
    rel_tol: float = 1e-15,
    max_terms: int = 500,
) -> float:
    """J(x, Delta, tau_m) = integral over [0, Delta] of exp(x e^{-t/tau_m}) dt in s
    (spec 5.4), where x = B (RH_in_start - RH_out). Series
    J = Delta + tau_m sum_{n>=1} x^n (1 - e^{-n Delta/tau_m}) / (n n!) for x >= -5; for
    x < -5 the exponential-integral difference tau_m (Ei(x) - Ei(x e^{-Delta/tau_m})).
    Relative error <= 1e-12 for |x| <= 50. Limits: tau_m = inf gives Delta e^x; tau_m = 0
    gives Delta. Raises ``ConvergenceError`` if the series does not meet ``rel_tol``
    within ``max_terms``."""
    _finite("x", x)
    _finite("duration_s", duration_s)
    if duration_s < 0.0:
        raise ModelDomainError(f"duration must be non-negative, got {duration_s!r}")
    if math.isnan(tau_m_s) or tau_m_s < 0.0:
        raise ModelDomainError(f"tau_m must be >= 0 (inf for sealed), got {tau_m_s!r}")
    value = _humidity_integral_array(
        np.array([x], dtype=np.float64),
        np.array([duration_s], dtype=np.float64),
        np.array([tau_m_s], dtype=np.float64),
        rel_tol,
        max_terms,
    )
    result = float(value[0])
    if not math.isfinite(result):
        raise ComputationError(f"humidity integral overflows for x={x!r}")
    return result


# --- ODE reference -----------------------------------------------------------------------------


class _CompletionEvent:
    """Terminal event of solve_ivp when alpha reaches 1 (kinds with finite completion)."""

    terminal = True
    direction = 1.0

    def __call__(self, t: float, y: FloatArray, k: float) -> float:
        return float(y[0]) - 1.0


def integrate_ode_reference(
    kind: RateLawKind,
    rate_constants_per_s: Sequence[float],
    durations_s: Sequence[float],
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
    rtol: float = 1e-11,
    atol: float = 1e-14,
) -> float:
    """Reference alpha at the end of the history by integrating d(alpha)/dt = k_i f(alpha)
    with scipy ``solve_ivp`` (Radau) restarted at every segment boundary (spec 5.9). For
    tests only: it must agree with ``conversion_from_theta`` within 1e-8 absolute on every
    kind away from singular starts (F-kinds with alpha0 = 0 are fine; B1 starts at its seed;
    diffusion kinds start at alpha = 1e-9 with the matching Theta offset)."""
    spec = _resolve_kind(kind, order_n, alpha0, avrami_n)
    if len(rate_constants_per_s) != len(durations_s):
        raise InputError("rate constants and durations must have the same length")
    # Diffusion kinds (f singular at 0) and Avrami kinds (f = 0 or singular at 0, so an ODE
    # started exactly at 0 never leaves it) start at a small alpha; the Theta that alpha
    # represents, g(alpha_start), is consumed from the start of the history so the end
    # point corresponds to the full Theta (docs/requests/B1.md R-02).
    singular = spec.kind.startswith("D") or _is_avrami(spec.kind)
    alpha = _ODE_SINGULAR_START_ALPHA if singular else 0.0
    offset = _g_scalar(spec, alpha) if singular else 0.0
    finite_completion = math.isfinite(_completion(spec))
    floor = alpha

    def rhs(_t: float, y: FloatArray, k: float) -> FloatArray:
        a = min(max(float(y[0]), floor), 1.0)
        value = 0.0 if a >= 1.0 else k * _f_scalar(spec, a)
        return np.array([value], dtype=np.float64)

    event = _CompletionEvent()
    for k, duration in zip(rate_constants_per_s, durations_s, strict=True):
        if not math.isfinite(k) or k < 0.0 or not math.isfinite(duration) or duration < 0.0:
            raise InputError("rate constants and durations must be finite and non-negative")
        if alpha >= 1.0:
            break
        if k == 0.0 or duration == 0.0:
            continue
        span = duration
        if offset > 0.0:
            needed = offset / k
            if needed >= span:
                offset -= k * span
                continue
            span -= needed
            offset = 0.0
        solution = solve_ivp(
            rhs,
            (0.0, span),
            np.array([alpha], dtype=np.float64),
            method="Radau",
            rtol=rtol,
            atol=atol,
            args=(k,),
            events=[event] if finite_completion else None,
        )
        if solution.status < 0:
            raise ConvergenceError(f"ODE reference failed: {solution.message}")
        if finite_completion and solution.status == 1:
            alpha = 1.0
            break
        alpha = min(float(solution.y[0, -1]), 1.0)
    return alpha


# =========================================================================================
# Vectorized evaluation over Monte Carlo draws
# =========================================================================================

_SEGMENT_CHUNK: Final[int] = 256
"""Segments evaluated together per pass (memory bound of the (segments x draws) arrays;
spec 12.5 allows segment-chunked evaluation). Structural: it never changes a result beyond
floating-point summation order, and it is fixed so runs are bit identical."""

_LIQUID_FAMILIES: Final[frozenset[str]] = frozenset({"solution", "suspension"})


@dataclasses.dataclass(frozen=True, slots=True)
class DrawBlock:
    """One batch of parameter draws for one ingredient (spec 9.7). Arrays have shape
    (n,) or (n, P) with P = len(model.pathways) in model order. Unused entries (for example
    ln k_vis of a thermal pathway) are 0.0 and never read. ``z_c0`` keeps the standard
    normal behind ``c0_percent`` for the assay re-anchor (spec 10.4).
    ``ln_k_dose_frozen_per_gy`` is the direct-effect constant used in frozen solution
    segments (spec 6.3); it equals ``ln_k_dose_per_gy`` when the pathway has none."""

    n: int
    c0_percent: FloatArray
    z_c0: FloatArray
    ln_k_ref_per_s: FloatArray
    ea_j_per_mol: FloatArray
    b_per_percent_rh: FloatArray
    ln_k_vis_per_lux_h: FloatArray
    ln_k_uv_per_w_h_per_m2: FloatArray
    ln_k_dose_per_gy: FloatArray
    ln_k_dose_frozen_per_gy: FloatArray
    ln_frozen_multiplier: FloatArray
    extrapolation_z_temperature: FloatArray
    extrapolation_z_humidity: FloatArray
    zone_ln_multiplier: Mapping[str, FloatArray]
    temperature_offset_k: Mapping[str, FloatArray]
    rh_offset_percent: Mapping[str, FloatArray]


@dataclasses.dataclass(frozen=True, slots=True)
class DrawResult:
    """Per-draw outcomes at as_of (spec 5.6). ``theta`` (n, P) is per pathway;
    ``alpha`` (n,) the destructive conversion; ``active_fraction`` (n,) rho;
    ``degradant_percent`` (n, P) in percent of label API mass (0 for pathways without a
    degradant); ``radiation_loss_fraction`` (n,) the central radiolysis loss reported
    separately (spec 6)."""

    theta: FloatArray
    alpha: FloatArray
    active_fraction: FloatArray
    potency_total_percent: FloatArray
    potency_active_percent: FloatArray
    degradant_percent: FloatArray
    radiation_loss_fraction: FloatArray


@dataclasses.dataclass(frozen=True, slots=True)
class NominalPathwayTheta:
    """Theta of one pathway at nominal parameters (means, Ea at its truncated-normal mean
    replaced by the location ``mu``), with the share accrued outside the validated range.

    The three ``segment_*`` members (one value per lot segment) are an addition so that
    ``placards_for``, which receives no microclimate trace, can state the humidity extent,
    the time spent and the share exactly (docs/requests/B1.md R-06): nominal Theta of the
    pathway per segment, d_RH of the pathway per segment (spec 8.1) and the larger nominal
    high-variant RH_in of the segment's start and end (deliquescence, spec 8.3)."""

    pathway_id: str
    theta: float
    theta_out_of_range: float
    theta_frozen: float
    theta_phase_change: float
    segment_theta: tuple[float, ...] = ()
    segment_d_rh_percent: tuple[float, ...] = ()
    segment_rh_in_max_percent: tuple[float, ...] = ()


def ln_rate_multiplier(prior_family: str, mu: float, sigma: float, z: FloatArray) -> FloatArray:
    """ln m of a zone rate multiplier for standard normals z (spec 9.7): lognormal
    mu + sigma z (mu and sigma in ln units, spec 2.3.3); fixed ln(mu), since a fixed prior's
    mu is the value itself (docs/requests/B1.md R-07). z = 0 gives the median. Other
    families raise ``ModelDomainError``."""
    if prior_family == "lognormal":
        return np.asarray(mu + sigma * z, dtype=np.float64)
    if prior_family == "fixed":
        if not mu > 0.0:
            raise ModelDomainError(f"a fixed rate multiplier must be positive, got {mu!r}")
        return np.full(np.shape(z), math.log(mu), dtype=np.float64)
    raise ModelDomainError(f"a zone rate multiplier must be lognormal or fixed, got {prior_family!r}")


def _parameter_index(pathway: Pathway, name: str) -> int | None:
    names = pathway.parameters.names
    return names.index(name) if name in names else None


def has_humidity_term(pathway: Pathway) -> bool:
    """True when the pathway's B is present and not fixed at 0 (spec 8.1)."""
    index = _parameter_index(pathway, "b_per_percent_rh")
    if index is None:
        return False
    block = pathway.parameters
    return not (block.mean[index] == 0.0 and block.covariance[index][index] == 0.0)


def _in_potency(pathway: Pathway, settings: EngineSettingsDoc) -> bool:
    """Destructive pathways enter alpha; radiolysis only when include_in_potency (spec 6.1)."""
    if pathway.role != "destructive":
        return False
    return pathway.driver != "radiation" or bool(settings["radiation"]["include_in_potency"])


@dataclasses.dataclass(frozen=True, slots=True)
class _SegmentTable:
    """Per-segment inputs of the vectorized evaluation, in segment order. Light and dose
    are stored as rates per second at the product so a partial segment scales exactly."""

    tau: FloatArray
    temperature: FloatArray
    rh_out_high: FloatArray
    rh_out_low: FloatArray
    rh_in_high: FloatArray
    rh_in_low: FloatArray
    rh_in_end_high: FloatArray
    tau_m: FloatArray
    t_offset_mask: FloatArray
    rh_offset_mask: FloatArray
    zone_index: NDArray[np.intp]
    zone_ids: tuple[str, ...]
    step: FloatArray
    dose_rate: FloatArray
    visible_rate: FloatArray
    uv_rate: FloatArray
    frozen: BoolArray
    phase_change: BoolArray


def _segment_table(
    model: StabilityModel,
    exposure: LotExposure,
    trace: MicroclimateTrace,
    step_responses: Mapping[str, Sequence[float]],
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
) -> _SegmentTable:
    segments = exposure.segments
    count = len(segments)
    if len(trace.rh_in_start_high) != count or len(trace.time_constant_s) != count:
        raise InputError(
            f"the microclimate trace has {len(trace.time_constant_s)} entries for {count} segments"
        )
    segment_zones = {s.zone_id for s in segments}
    zone_ids = tuple(sorted(segment_zones | set(step_responses)))
    for zone_id in zone_ids:
        if zone_id not in zones:
            raise InputError(f"unknown zone {zone_id!r}")
    for zone_id in sorted(segment_zones):
        if zone_id not in step_responses:
            raise InputError(f"no unit step response for zone {zone_id!r}")
    step = np.zeros((len(zone_ids), count), dtype=np.float64)
    for row, zone_id in enumerate(zone_ids):
        if zone_id in step_responses:
            values = np.asarray(step_responses[zone_id], dtype=np.float64)
            if values.shape != (count,):
                raise InputError(
                    f"the step response of zone {zone_id!r} has {values.size} values for {count} segments"
                )
            step[row] = values
    zone_position = {zone_id: index for index, zone_id in enumerate(zone_ids)}
    tau = np.array([s.t1_s - s.t0_s for s in segments], dtype=np.float64)
    if bool(np.any(tau < 0.0)):
        raise InputError("segments must not have negative durations")
    temperature = np.array([s.temperature_k for s in segments], dtype=np.float64)
    light = np.array([zones[s.zone_id]["light_exposure"] for s in segments], dtype=np.bool_)
    transmission_vis = np.empty(count, dtype=np.float64)
    transmission_uv = np.empty(count, dtype=np.float64)
    for index, segment in enumerate(segments):
        if segment.package_id is None or segment.package_id not in packages:
            raise ModelDomainError(f"segment {index} has no known package")
        package = packages[segment.package_id]
        transmission_vis[index] = package["light_transmission_visible"]["value"]
        transmission_uv[index] = package["light_transmission_uv"]["value"]
    illuminance = np.array([s.illuminance_lux for s in segments], dtype=np.float64)
    uv = np.array([s.uv_w_per_m2 for s in segments], dtype=np.float64)
    liquid = model.dose_form_family in _LIQUID_FAMILIES
    freezing = model.freezing_point_k
    phase = model.phase_change_temperature_k
    no = np.zeros(count, dtype=np.bool_)
    frozen = temperature < freezing if liquid and freezing is not None else no
    phase_change = temperature > phase if phase is not None else no
    return _SegmentTable(
        tau=tau,
        temperature=temperature,
        rh_out_high=np.array([s.relative_humidity_percent for s in segments], dtype=np.float64),
        rh_out_low=np.array([s.relative_humidity_low_percent for s in segments], dtype=np.float64),
        rh_in_high=np.array(trace.rh_in_start_high, dtype=np.float64),
        rh_in_low=np.array(trace.rh_in_start_low, dtype=np.float64),
        rh_in_end_high=np.array(trace.rh_in_end_high, dtype=np.float64),
        tau_m=np.array(trace.time_constant_s, dtype=np.float64),
        t_offset_mask=np.array([0.0 if "temperature_k" in s.gap_quantities else 1.0 for s in segments]),
        rh_offset_mask=np.array([0.0 if "relative_humidity" in s.gap_quantities else 1.0 for s in segments]),
        zone_index=np.array([zone_position[s.zone_id] for s in segments], dtype=np.intp),
        zone_ids=zone_ids,
        step=step,
        dose_rate=np.array([s.dose_rate_gy_per_s for s in segments], dtype=np.float64),
        visible_rate=np.where(light, transmission_vis * illuminance / SECONDS_PER_HOUR, 0.0),
        uv_rate=np.where(light, transmission_uv * uv / SECONDS_PER_HOUR, 0.0),
        frozen=np.asarray(frozen, dtype=np.bool_),
        phase_change=np.asarray(phase_change, dtype=np.bool_),
    )


def _temperature_min(pathway: Pathway, settings: EngineSettingsDoc) -> float:
    minimum = pathway.validated_range.temperature_min_k
    if any(e.source_layer == "measured_model" for e in pathway.parameters.estimates):
        if "validated_range" not in settings:
            raise ModelDomainError("measured models require validated_range settings")
        minimum -= settings["validated_range"]["temperature_below_min_margin_k"]["value"]
    return minimum


def _range_distances(
    pathway: Pathway, table: _SegmentTable, settings: EngineSettingsDoc
) -> tuple[FloatArray, FloatArray]:
    """d_T and d_RH of spec 8.1 per segment from nominal values: d_RH over the nominal
    high-variant RH_in at the segment start and end, the larger of the two."""
    validated = pathway.validated_range
    temperature = table.temperature
    d_t = np.maximum(
        0.0,
        np.maximum(
            temperature - validated.temperature_max_k, _temperature_min(pathway, settings) - temperature
        ),
    )
    if not has_humidity_term(pathway):
        return d_t, np.zeros_like(d_t)
    distances = [
        np.maximum(0.0, np.maximum(rh - validated.rh_max_percent, validated.rh_min_percent - rh))
        for rh in (table.rh_in_high, table.rh_in_end_high)
    ]
    return d_t, np.maximum(distances[0], distances[1])


@dataclasses.dataclass(frozen=True, slots=True)
class _Context:
    """Everything of one evaluation that does not depend on the segment chunk."""

    model: StabilityModel
    table: _SegmentTable
    draws: DrawBlock
    settings: EngineSettingsDoc
    ln_multiplier: FloatArray
    t_offset: FloatArray
    rh_offset: FloatArray
    d_t: tuple[FloatArray, ...]
    d_rh: tuple[FloatArray, ...]
    in_potency: tuple[bool, ...]
    racemization: tuple[bool, ...]


_DRAW_MATRICES: Final[tuple[str, ...]] = (
    "ln_k_ref_per_s",
    "ea_j_per_mol",
    "b_per_percent_rh",
    "ln_k_vis_per_lux_h",
    "ln_k_uv_per_w_h_per_m2",
    "ln_k_dose_per_gy",
    "ln_k_dose_frozen_per_gy",
    "ln_frozen_multiplier",
    "extrapolation_z_temperature",
    "extrapolation_z_humidity",
)


def _context(
    model: StabilityModel,
    table: _SegmentTable,
    draws: DrawBlock,
    settings: EngineSettingsDoc,
) -> _Context:
    n = draws.n
    pathways = model.pathways
    count = len(pathways)
    for name in ("c0_percent", "z_c0"):
        if np.shape(getattr(draws, name)) != (n,):
            raise InputError(f"DrawBlock.{name} must have shape ({n},)")
    for name in _DRAW_MATRICES:
        if np.shape(getattr(draws, name)) != (n, count):
            raise InputError(f"DrawBlock.{name} must have shape ({n}, {count})")

    def zone_matrix(mapping: Mapping[str, FloatArray], label: str) -> FloatArray:
        out = np.zeros((len(table.zone_ids), n), dtype=np.float64)
        for row, zone_id in enumerate(table.zone_ids):
            if zone_id not in mapping:
                raise InputError(f"DrawBlock.{label} has no entry for zone {zone_id!r}")
            values = np.asarray(mapping[zone_id], dtype=np.float64)
            if values.shape != (n,):
                raise InputError(f"DrawBlock.{label}[{zone_id!r}] must have shape ({n},)")
            out[row] = values
        return out

    distances = [_range_distances(pathway, table, settings) for pathway in pathways]
    return _Context(
        model=model,
        table=table,
        draws=draws,
        settings=settings,
        ln_multiplier=zone_matrix(draws.zone_ln_multiplier, "zone_ln_multiplier"),
        t_offset=zone_matrix(draws.temperature_offset_k, "temperature_offset_k"),
        rh_offset=zone_matrix(draws.rh_offset_percent, "rh_offset_percent"),
        d_t=tuple(d for d, _ in distances),
        d_rh=tuple(d for _, d in distances),
        in_potency=tuple(_in_potency(pathway, settings) for pathway in pathways),
        racemization=tuple(pathway.role == "racemization" for pathway in pathways),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _View:
    """Segment inputs gathered for one evaluation and broadcastable against per-draw
    arrays: shape (S_c, 1) for a block of segments against all draws (zone variables
    (S_c, n)), or (1, m) when each of m draws has its own segment."""

    tau: FloatArray
    temperature: FloatArray
    rh_out_high: FloatArray
    rh_out_low: FloatArray
    rh_in_high: FloatArray
    rh_in_low: FloatArray
    tau_m: FloatArray
    t_mask: FloatArray
    e_mask: FloatArray
    frozen: BoolArray
    dose_rate: FloatArray
    visible_rate: FloatArray
    uv_rate: FloatArray
    d_t: tuple[FloatArray, ...]
    d_rh: tuple[FloatArray, ...]
    ln_multiplier: FloatArray
    t_offset: FloatArray
    rh_offset: FloatArray
    step_shift: FloatArray


def _grid_view(context: _Context, lo: int, hi: int) -> _View:
    table = context.table
    rows = table.zone_index[lo:hi]

    def seg(values: FloatArray) -> FloatArray:
        return values[lo:hi, None]

    step_shift = np.zeros((hi - lo, context.draws.n), dtype=np.float64)
    for row in range(len(table.zone_ids)):
        step_shift = step_shift + table.step[row, lo:hi, None] * context.rh_offset[row][None, :]
    return _View(
        tau=seg(table.tau),
        temperature=seg(table.temperature),
        rh_out_high=seg(table.rh_out_high),
        rh_out_low=seg(table.rh_out_low),
        rh_in_high=seg(table.rh_in_high),
        rh_in_low=seg(table.rh_in_low),
        tau_m=seg(table.tau_m),
        t_mask=seg(table.t_offset_mask),
        e_mask=seg(table.rh_offset_mask),
        frozen=table.frozen[lo:hi, None],
        dose_rate=seg(table.dose_rate),
        visible_rate=seg(table.visible_rate),
        uv_rate=seg(table.uv_rate),
        d_t=tuple(d[lo:hi, None] for d in context.d_t),
        d_rh=tuple(d[lo:hi, None] for d in context.d_rh),
        ln_multiplier=context.ln_multiplier[rows],
        t_offset=context.t_offset[rows],
        rh_offset=context.rh_offset[rows],
        step_shift=step_shift,
    )


def _diagonal_view(context: _Context, segment_index: NDArray[np.intp]) -> _View:
    table = context.table
    rows = table.zone_index[segment_index]
    draws = np.arange(segment_index.size)

    def seg(values: FloatArray) -> FloatArray:
        return values[segment_index][None, :]

    step_shift = np.zeros((1, segment_index.size), dtype=np.float64)
    for row in range(len(table.zone_ids)):
        step_shift = step_shift + table.step[row, segment_index][None, :] * context.rh_offset[row][None, :]
    return _View(
        tau=seg(table.tau),
        temperature=seg(table.temperature),
        rh_out_high=seg(table.rh_out_high),
        rh_out_low=seg(table.rh_out_low),
        rh_in_high=seg(table.rh_in_high),
        rh_in_low=seg(table.rh_in_low),
        tau_m=seg(table.tau_m),
        t_mask=seg(table.t_offset_mask),
        e_mask=seg(table.rh_offset_mask),
        frozen=table.frozen[segment_index][None, :],
        dose_rate=seg(table.dose_rate),
        visible_rate=seg(table.visible_rate),
        uv_rate=seg(table.uv_rate),
        d_t=tuple(d[segment_index][None, :] for d in context.d_t),
        d_rh=tuple(d[segment_index][None, :] for d in context.d_rh),
        ln_multiplier=context.ln_multiplier[rows, draws][None, :],
        t_offset=context.t_offset[rows, draws][None, :],
        rh_offset=context.rh_offset[rows, draws][None, :],
        step_shift=step_shift,
    )


def _thermal_parts(context: _Context, view: _View, p: int) -> tuple[FloatArray, FloatArray]:
    """ln k_p,seg (the exponent) and x = B_p b'_seg of a thermal pathway (spec 5.6), so
    Theta over a duration tau is exp(exponent) J(x, tau, tau_m)."""
    draws = context.draws
    model = context.model
    pathway = model.pathways[p]
    if _parameter_index(pathway, "ln_k_ref_per_s") is None:
        raise ModelDomainError(f"thermal pathway {pathway.pathway_id!r} has no ln_k_ref_per_s")
    b = draws.b_per_percent_rh[:, p][None, :]
    negative = b < 0.0
    rh_out = np.where(negative, view.rh_out_low, view.rh_out_high)
    rh_in = np.where(negative, view.rh_in_low, view.rh_in_high)
    rh_out_draw = rh_out + view.rh_offset * view.e_mask
    b_prime = (rh_in + view.step_shift) - rh_out_draw
    temperature = view.temperature + view.t_offset * view.t_mask
    if bool(np.any(temperature <= 0.0)):
        raise ComputationError("a drawn temperature is not positive")
    t_ref = model.reference_temperature_k
    inverse_gap = (t_ref - temperature) / (temperature * t_ref)
    extrapolation = context.settings["extrapolation"]
    widening = (
        draws.extrapolation_z_temperature[:, p][None, :]
        * float(extrapolation["ln_k_sd_per_kelvin"]["value"])
        * view.d_t[p]
        + draws.extrapolation_z_humidity[:, p][None, :]
        * float(extrapolation["ln_k_sd_per_percent_rh"]["value"])
        * view.d_rh[p]
    )
    exponent = (
        draws.ln_k_ref_per_s[:, p][None, :]
        + view.ln_multiplier
        + np.where(view.frozen, draws.ln_frozen_multiplier[:, p][None, :], 0.0)
        + widening
        - (draws.ea_j_per_mol[:, p][None, :] / GAS_CONSTANT_J_PER_MOL_K) * inverse_gap
        + b * (rh_out_draw - model.reference_rh_percent)
    )
    return np.asarray(exponent, dtype=np.float64), np.asarray(b * b_prime, dtype=np.float64)


def _humidity_settings(context: _Context) -> tuple[float, int]:
    numerics = context.settings["numerics"]
    return float(numerics["series_relative_tolerance"]["value"]), int(numerics["series_max_terms"]["value"])


def _linear_rate(context: _Context, view: _View, p: int) -> FloatArray:
    """Theta per second of a photolysis (spec 5.5) or radiolysis (spec 6.2, 6.3) pathway:
    no Arrhenius factor and no zone multiplier; the direct-effect constant in frozen
    solution segments."""
    pathway = context.model.pathways[p]
    draws = context.draws
    with np.errstate(over="ignore"):
        if pathway.driver == "light":
            has_vis = _parameter_index(pathway, "ln_k_vis_per_lux_h") is not None
            has_uv = _parameter_index(pathway, "ln_k_uv_per_w_h_per_m2") is not None
            if not (has_vis or has_uv):
                raise ModelDomainError(
                    f"light pathway {pathway.pathway_id!r} has neither ln_k_vis nor ln_k_uv"
                )
            rate = np.zeros(np.broadcast_shapes(view.visible_rate.shape, (1, draws.n)), dtype=np.float64)
            if has_vis:
                rate = rate + view.visible_rate * np.exp(draws.ln_k_vis_per_lux_h[:, p][None, :])
            if has_uv:
                rate = rate + view.uv_rate * np.exp(draws.ln_k_uv_per_w_h_per_m2[:, p][None, :])
            return rate
        if _parameter_index(pathway, "ln_k_dose_per_gy") is None:
            raise ModelDomainError(f"radiolysis pathway {pathway.pathway_id!r} has no ln_k_dose_per_gy")
        ln_k = np.where(
            view.frozen, draws.ln_k_dose_frozen_per_gy[:, p][None, :], draws.ln_k_dose_per_gy[:, p][None, :]
        )
        return np.asarray(view.dose_rate * np.exp(ln_k), dtype=np.float64)


def _humidity_grid(
    x: FloatArray, duration: FloatArray, tau_m: FloatArray, rel_tol: float, max_terms: int
) -> FloatArray:
    """Evaluate shared per-segment decays once, before broadcasting over draws."""
    if duration.ndim != 2 or duration.shape[1] != 1 or tau_m.shape != duration.shape:
        return _humidity_integral_array(x, duration, tau_m, rel_tol, max_terms)
    d, tau = duration[:, 0], tau_m[:, 0]
    exchange = (tau > 0.0) & np.isfinite(tau) & (d > 0.0)
    delta = np.divide(d, tau, out=np.zeros_like(d), where=exchange)
    span = np.max(np.abs(x), axis=1) * -np.expm1(-delta)
    eligible = exchange & (delta <= _QUADRATURE_DELTA) & (span <= _QUADRATURE_SPAN)
    if not bool(np.any(eligible)):
        return _humidity_integral_array(x, duration, tau_m, rel_tol, max_terms)
    result = np.empty_like(x)
    other = ~eligible
    if bool(np.any(other)):
        result[other] = _humidity_integral_array(x[other], duration[other], tau_m[other], rel_tol, max_terms)
    measure = np.maximum(span, delta)
    remaining = eligible.copy()
    for limit, count in (*_GAUSS_LEGENDRE_LADDER, (math.inf, _GAUSS_LEGENDRE_NODES)):
        rows = remaining & (measure <= limit)
        if bool(np.any(rows)):
            nodes, weights = _gauss_legendre(count)
            total = np.zeros_like(x[rows])
            half = 0.5 * d[rows, None]
            for node, weight in zip(nodes, weights, strict=True):
                decay = np.exp(-half * (node + 1.0) / tau[rows, None])
                total += weight * np.exp(x[rows] * decay)
            result[rows] = half * total
            remaining &= ~rows
    return result


def _pathway_theta(context: _Context, view: _View, p: int, tau: FloatArray) -> FloatArray:
    """Theta of pathway p over durations ``tau`` (the segments' own durations, or a
    partial duration per draw)."""
    if context.model.pathways[p].driver == "thermal":
        exponent, x = _thermal_parts(context, view, p)
        rel_tol, max_terms = _humidity_settings(context)
        j_value = _humidity_grid(x, tau, view.tau_m, rel_tol, max_terms)
        with np.errstate(over="ignore"):
            theta = np.exp(exponent) * j_value
    else:
        theta = _linear_rate(context, view, p) * tau
    if not bool(np.all(np.isfinite(theta))):
        raise ComputationError(f"Theta of pathway {context.model.pathways[p].pathway_id!r} overflows float64")
    return np.asarray(theta, dtype=np.float64)


def _conversion(model: StabilityModel, theta: FloatArray, settings: EngineSettingsDoc) -> FloatArray:
    flat = np.ascontiguousarray(theta, dtype=np.float64).reshape(-1)
    alpha = conversion_from_theta_array(
        model.rate_law_kind,
        flat,
        order_n=model.order_n,
        alpha0=model.alpha0,
        avrami_n=model.avrami_n,
        numerics=settings["numerics"],
    )
    return alpha.reshape(np.shape(theta))


def active_fraction(theta_racemization: FloatArray, initial_active_fraction: float) -> FloatArray:
    """rho(Theta_r) = (1 + (2 f0 - 1) e^(-2 Theta_r)) / 2 (spec 5.7)."""
    return np.asarray(
        0.5 * (1.0 + (2.0 * initial_active_fraction - 1.0) * np.exp(-2.0 * np.asarray(theta_racemization))),
        dtype=np.float64,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _Accumulated:
    theta: FloatArray
    degradant_alpha: FloatArray
    segment_theta: tuple[FloatArray, ...] | None
    segment_sums: tuple[FloatArray, FloatArray] | None


def _ordered_chunks(
    work: Callable[[int], tuple[int, int, list[FloatArray]]], segments: int, settings: EngineSettingsDoc
) -> Iterator[tuple[int, int, list[FloatArray]]]:
    if "worker_threads" not in settings["numerics"]:
        raise ModelDomainError("evaluation requires numerics.worker_threads")
    configured = settings["numerics"]["worker_threads"]["value"]
    if not math.isfinite(configured) or configured < 0 or int(configured) != configured:
        raise ModelDomainError("worker_threads must be a non-negative integer")
    workers = int(configured) or os.cpu_count() or 1
    workers = min(workers, max(1, math.ceil(segments / _SEGMENT_CHUNK)))
    starts = range(0, segments, _SEGMENT_CHUNK)
    manager = ThreadPoolExecutor(max_workers=workers) if workers > 1 else nullcontext(None)
    with manager as pool:
        # A bounded queue prevents completed chunks from retaining the whole history.
        results = map(work, starts) if pool is None else pool.map(work, starts, buffersize=workers)
        yield from results


def _accumulate(
    context: _Context,
    *,
    keep_segments: bool = False,
    keep_sums: bool = False,
    chunk_provider: Callable[[int, int], list[FloatArray]] | None = None,
) -> _Accumulated:
    model = context.model
    settings = context.settings
    n = context.draws.n
    count = len(model.pathways)
    segments = context.table.tau.size
    in_potency = context.in_potency
    attribute = [
        not keep_sums and in_potency[p] and model.pathways[p].degradant is not None for p in range(count)
    ]
    theta = np.zeros((n, count), dtype=np.float64)
    degradant_alpha = np.zeros((n, count), dtype=np.float64)
    kept: list[list[FloatArray]] = [[] for _ in range(count)]
    sums_d: list[FloatArray] = []
    sums_r: list[FloatArray] = []
    cumulative = np.zeros(n, dtype=np.float64)
    alpha_before = np.zeros(n, dtype=np.float64)

    def work(lo: int) -> tuple[int, int, list[FloatArray]]:
        hi = min(segments, lo + _SEGMENT_CHUNK)
        if chunk_provider is not None:
            return lo, hi, chunk_provider(lo, hi)
        view = _grid_view(context, lo, hi)
        return lo, hi, [_pathway_theta(context, view, p, view.tau) for p in range(count)]

    for lo, hi, chunks in _ordered_chunks(work, segments, settings):
        for p, chunk in enumerate(chunks):
            theta[:, p] += np.sum(chunk, axis=0)
            if keep_segments:
                kept[p].append(chunk)
        segment_total = np.zeros((hi - lo, n), dtype=np.float64)
        for p in range(count):
            if in_potency[p]:
                segment_total = segment_total + chunks[p]
        if keep_sums:
            racemization_total = np.zeros((hi - lo, n), dtype=np.float64)
            for p in range(count):
                if context.racemization[p]:
                    racemization_total = racemization_total + chunks[p]
            sums_d.append(segment_total)
            sums_r.append(racemization_total)
        if any(attribute):
            # Exact per-segment attribution of the conversion increment (spec 5.6).
            running = cumulative[None, :] + np.cumsum(np.ascontiguousarray(segment_total.T), axis=1).T
            alpha_running = _conversion(model, running, settings)
            previous = np.vstack([alpha_before[None, :], alpha_running[:-1]])
            increment = alpha_running - previous
            safe_total = np.where(segment_total > 0.0, segment_total, 1.0)
            for p in range(count):
                if attribute[p]:
                    share = np.where(segment_total > 0.0, chunks[p] / safe_total, 0.0)
                    degradant_alpha[:, p] += np.sum(share * increment, axis=0)
            cumulative = running[-1]
            alpha_before = alpha_running[-1]

    def joined(parts: list[FloatArray]) -> FloatArray:
        return np.concatenate(parts, axis=0) if parts else np.zeros((0, n), dtype=np.float64)

    return _Accumulated(
        theta=theta,
        degradant_alpha=degradant_alpha,
        segment_theta=tuple(joined(parts) for parts in kept) if keep_segments else None,
        segment_sums=(joined(sums_d), joined(sums_r)) if keep_sums else None,
    )


def slice_draws(draws: DrawBlock, index: NDArray[np.intp]) -> DrawBlock:
    """The draws selected by ``index`` (in that order) as a DrawBlock."""
    picked = np.asarray(index, dtype=np.intp)

    def rows(values: FloatArray) -> FloatArray:
        return np.asarray(values[picked], dtype=np.float64)

    return DrawBlock(
        n=int(picked.size),
        c0_percent=rows(draws.c0_percent),
        z_c0=rows(draws.z_c0),
        ln_k_ref_per_s=rows(draws.ln_k_ref_per_s),
        ea_j_per_mol=rows(draws.ea_j_per_mol),
        b_per_percent_rh=rows(draws.b_per_percent_rh),
        ln_k_vis_per_lux_h=rows(draws.ln_k_vis_per_lux_h),
        ln_k_uv_per_w_h_per_m2=rows(draws.ln_k_uv_per_w_h_per_m2),
        ln_k_dose_per_gy=rows(draws.ln_k_dose_per_gy),
        ln_k_dose_frozen_per_gy=rows(draws.ln_k_dose_frozen_per_gy),
        ln_frozen_multiplier=rows(draws.ln_frozen_multiplier),
        extrapolation_z_temperature=rows(draws.extrapolation_z_temperature),
        extrapolation_z_humidity=rows(draws.extrapolation_z_humidity),
        zone_ln_multiplier={k: rows(v) for k, v in sorted(draws.zone_ln_multiplier.items())},
        temperature_offset_k={k: rows(v) for k, v in sorted(draws.temperature_offset_k.items())},
        rh_offset_percent={k: rows(v) for k, v in sorted(draws.rh_offset_percent.items())},
    )


def projected_thetas(
    model: StabilityModel,
    exposure: LotExposure,
    trace: MicroclimateTrace,
    step_responses: Mapping[str, Sequence[float]],
    draws: DrawBlock,
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    settings: EngineSettingsDoc,
) -> tuple[FloatArray, FloatArray]:
    """Per-segment Theta of every draw, summed over the pathways that enter alpha and over
    the racemization pathways: two arrays of shape (segments, n). The forecast (spec 11.4)
    marches these over the projected segments; the formulas are those of evaluate_draws."""
    table = _segment_table(model, exposure, trace, step_responses, zones, packages)
    accumulated = _accumulate(_context(model, table, draws, settings), keep_sums=True)
    if accumulated.segment_sums is None:  # pragma: no cover - keep_sums=True always keeps them
        raise ComputationError("segment sums missing")
    return accumulated.segment_sums


@dataclasses.dataclass(frozen=True, slots=True)
class PartialSegment:
    """One segment per draw, ready for Theta over a partial duration (forecast bisection,
    spec 11.4): thermal pathways contribute exp(exponent) J(x, tau, tau_m), light and
    radiolysis pathways rate x tau. Arrays have shape (m,) for m draws; entries of the
    tuples are None where a pathway does not use them."""

    exponent: tuple[FloatArray | None, ...]
    x: tuple[FloatArray | None, ...]
    rate: tuple[FloatArray | None, ...]
    tau_m: FloatArray
    in_potency: tuple[bool, ...]
    racemization: tuple[bool, ...]
    rel_tol: float
    max_terms: int

    def select(self, index: NDArray[np.intp]) -> PartialSegment:
        """The draws selected by ``index``, in that order."""
        picked = np.asarray(index, dtype=np.intp)

        def take(values: tuple[FloatArray | None, ...]) -> tuple[FloatArray | None, ...]:
            return tuple(None if v is None else np.asarray(v[picked], dtype=np.float64) for v in values)

        return dataclasses.replace(
            self,
            exponent=take(self.exponent),
            x=take(self.x),
            rate=take(self.rate),
            tau_m=np.asarray(self.tau_m[picked], dtype=np.float64),
        )


def concatenate_partials(parts: Sequence[PartialSegment]) -> PartialSegment:
    """Join the draws of several PartialSegments of the same model, in order."""
    if not parts:
        raise InputError("at least one partial segment is required")
    first = parts[0]
    for part in parts[1:]:
        if (part.in_potency, part.racemization) != (first.in_potency, first.racemization):
            raise InputError("partial segments of different models cannot be joined")

    def joined(member: str) -> tuple[FloatArray | None, ...]:
        out: list[FloatArray | None] = []
        for p in range(len(first.in_potency)):
            values = [getattr(part, member)[p] for part in parts]
            if any(v is None for v in values):
                out.append(None)
            else:
                out.append(np.concatenate([np.asarray(v, dtype=np.float64) for v in values if v is not None]))
        return tuple(out)

    return dataclasses.replace(
        first,
        exponent=joined("exponent"),
        x=joined("x"),
        rate=joined("rate"),
        tau_m=np.concatenate([part.tau_m for part in parts]),
    )


def partial_segment(
    model: StabilityModel,
    exposure: LotExposure,
    trace: MicroclimateTrace,
    step_responses: Mapping[str, Sequence[float]],
    draws: DrawBlock,
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    settings: EngineSettingsDoc,
    segment_index: NDArray[np.intp],
) -> PartialSegment:
    """The pieces of segment ``segment_index[j]`` for draw j of ``draws``."""
    index = np.asarray(segment_index, dtype=np.intp)
    if index.shape != (draws.n,):
        raise InputError("one segment index per draw is required")
    table = _segment_table(model, exposure, trace, step_responses, zones, packages)
    if index.size and (int(index.min()) < 0 or int(index.max()) >= table.tau.size):
        raise InputError("segment index out of range")
    context = _context(model, table, draws, settings)
    view = _diagonal_view(context, index)
    exponents: list[FloatArray | None] = []
    xs: list[FloatArray | None] = []
    rates: list[FloatArray | None] = []
    for p, pathway in enumerate(model.pathways):
        if pathway.driver == "thermal":
            exponent, x = _thermal_parts(context, view, p)
            exponents.append(exponent[0])
            xs.append(x[0])
            rates.append(None)
        else:
            exponents.append(None)
            xs.append(None)
            rates.append(_linear_rate(context, view, p)[0])
    rel_tol, max_terms = _humidity_settings(context)
    return PartialSegment(
        exponent=tuple(exponents),
        x=tuple(xs),
        rate=tuple(rates),
        tau_m=view.tau_m[0],
        in_potency=context.in_potency,
        racemization=context.racemization,
        rel_tol=rel_tol,
        max_terms=max_terms,
    )


def partial_thetas(partial: PartialSegment, tau: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Theta over the first ``tau`` seconds of each draw's segment, summed over the
    pathways in alpha and over the racemization pathways."""
    duration = np.asarray(tau, dtype=np.float64)
    destructive = np.zeros_like(duration)
    racemization = np.zeros_like(duration)
    for p in range(len(partial.in_potency)):
        exponent = partial.exponent[p]
        x = partial.x[p]
        rate = partial.rate[p]
        if exponent is not None and x is not None:
            j_value = _humidity_integral_array(x, duration, partial.tau_m, partial.rel_tol, partial.max_terms)
            with np.errstate(over="ignore"):
                theta = np.exp(exponent) * j_value
        elif rate is not None:
            theta = rate * duration
        else:  # pragma: no cover - partial_segment fills one of the two forms
            raise ComputationError("incomplete partial segment")
        if not bool(np.all(np.isfinite(theta))):
            raise ComputationError("partial-segment Theta overflows float64")
        if partial.in_potency[p]:
            destructive = destructive + theta
        if partial.racemization[p]:
            racemization = racemization + theta
    return destructive, racemization


def evaluate_draws(
    model: StabilityModel,
    exposure: LotExposure,
    trace: MicroclimateTrace,
    step_responses: Mapping[str, Sequence[float]],
    draws: DrawBlock,
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    settings: EngineSettingsDoc,
) -> DrawResult:
    """Evaluate every draw over the lot exposure (spec 5.6). Per segment s and pathway p:

    thermal: Theta_p += exp(ln k_p + ln m_zone + [frozen] ln m_frozen + extrapolation terms)
             x exp(-(Ea_p/R)(1/(T_s + dT_zone) - 1/T*)) x exp(B_p (RH_out' - RH*))
             x J(B_p b'_s, tau_s, tau_m,s) with RH_out' = RH_out + e_s dRH_zone and
             b'_s = RH_in,start + sum_z dRH_z h_z,s - RH_out' (spec 4.4, 5.4, 5.6);
             ``step_responses`` maps zone_id to h_z at segment starts
    light:   Theta_p += (k_vis T_vis E_s + k_uv T_uv U_s) tau_s / 3600 when the zone has
             light exposure (spec 5.5)
    radiation: only when settings.radiation.include_in_potency (spec 6)

    The destructive pathways of the ingredient share one conversion: alpha = G(sum Theta).
    Racemization pathways set rho (spec 5.7). Degradant attribution is exact per segment
    (spec 5.6). Deterministic: no random numbers are drawn here.
    """
    if draws.n <= 0:
        raise InputError("a DrawBlock needs at least one draw")
    table = _segment_table(model, exposure, trace, step_responses, zones, packages)
    context = _context(model, table, draws, settings)
    accumulated = _accumulate(context, keep_segments=False)
    return _result(model, draws, accumulated, settings)


@dataclasses.dataclass(frozen=True, slots=True)
class CheckedDrawResult:
    """Checked history evaluation and its deterministic numerical diagnostics."""

    result: DrawResult
    history_evaluator: str
    probe_relative_error: float | None
    fell_back: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _RateDictionary:
    table: _SegmentTable
    inverse: NDArray[np.intp]
    scale: FloatArray


def _take_table(table: _SegmentTable, index: NDArray[np.intp]) -> _SegmentTable:
    values: dict[str, object] = {}
    for field in dataclasses.fields(table):
        value = getattr(table, field.name)
        values[field.name] = (
            (value[:, index] if field.name == "step" else value[index])
            if isinstance(value, np.ndarray)
            else value
        )
    return _SegmentTable(**values)  # type: ignore[arg-type]


def _rate_dictionary(context: _Context, p: int) -> _RateDictionary | None:
    """Lossless dictionary of rate drivers; duration is factored out for memoryless rates."""
    path = context.model.pathways[p]
    if path.driver != "thermal":
        return None
    table = context.table
    no_humidity = not has_humidity_term(path)
    sealed = np.isinf(table.tau_m)
    memoryless = (table.tau_m == 0.0) | sealed | no_humidity
    tau = np.where(memoryless, 1.0, table.tau)
    rh_high = np.zeros_like(tau) if no_humidity else np.where(sealed, table.rh_in_high, table.rh_out_high)
    rh_low = np.zeros_like(tau) if no_humidity else np.where(sealed, table.rh_in_low, table.rh_out_low)
    canonical = dataclasses.replace(
        table,
        tau=tau,
        rh_out_high=rh_high,
        rh_out_low=rh_low,
        rh_in_high=np.where(memoryless, rh_high, table.rh_in_high),
        rh_in_low=np.where(memoryless, rh_low, table.rh_in_low),
        tau_m=np.where(memoryless, 0.0, table.tau_m),
        rh_offset_mask=np.where(sealed | no_humidity, 0.0, table.rh_offset_mask),
        step=np.where((sealed | no_humidity)[None, :], 0.0, table.step),
    )
    columns = [
        canonical.tau,
        canonical.temperature,
        canonical.rh_out_high,
        canonical.rh_out_low,
        canonical.rh_in_high,
        canonical.rh_in_low,
        canonical.tau_m,
        canonical.t_offset_mask,
        canonical.rh_offset_mask,
        canonical.zone_index,
        canonical.frozen,
        context.d_t[p],
        context.d_rh[p],
        *canonical.step,
    ]
    _, first, inverse = np.unique(np.column_stack(columns), axis=0, return_index=True, return_inverse=True)
    if first.size == table.tau.size or first.size > _SEGMENT_CHUNK:
        return None
    return _RateDictionary(
        _take_table(canonical, first.astype(np.intp)),
        inverse.astype(np.intp),
        np.where(memoryless, table.tau, 1.0),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _TemperatureGroup:
    table: _SegmentTable
    segments: NDArray[np.intp]
    weights: FloatArray


@dataclasses.dataclass(frozen=True, slots=True)
class _TemperatureInterpolation:
    groups: tuple[_TemperatureGroup, ...]
    scale: FloatArray


def _interpolation_degree(
    context: _Context, p: int, indices: NDArray[np.intp], lo: float, hi: float
) -> int | None:
    """Bernstein ellipse bound for exp(Ea/R (1/c - 1/T) + slope (T-c))."""
    center, half = (hi + lo) / 2.0, (hi - lo) / 2.0
    if half == 0.0:
        return 0
    table = context.table
    row = int(table.zone_index[indices[0]])
    offset = context.t_offset[row] * table.t_offset_mask[indices[0]]
    minimum = center + float(np.min(offset))
    if minimum <= half:
        return None  # the exact evaluator reports the nonpositive drawn temperature
    a = float(np.max(np.abs(context.draws.ea_j_per_mol[:, p]))) / GAS_CONSTANT_J_PER_MOL_K
    outside = bool(np.any(context.d_t[p][indices] > 0.0))
    slope = (
        float(np.max(np.abs(context.draws.extrapolation_z_temperature[:, p])))
        * context.settings["extrapolation"]["ln_k_sd_per_kelvin"]["value"]
        if outside
        else 0.0
    )
    real_bound = a * half / (minimum * (minimum - half)) + slope * half
    tolerance = context.settings["numerics"]["compression_relative_tolerance"]["value"]
    if tolerance <= 0.0 or not math.isfinite(real_bound):
        return None
    # A conservative arithmetic allowance precedes the analytic approximation budget.
    draws = context.draws
    exponent_scale = (
        float(np.max(np.abs(draws.ln_k_ref_per_s[:, p])))
        + float(np.max(np.abs(context.ln_multiplier[row])))
        + a * (1.0 / (minimum - half) + 1.0 / context.model.reference_temperature_k)
        + float(np.max(np.abs(draws.b_per_percent_rh[:, p])))
        * (2.0 * PERCENT + float(np.sum(np.max(np.abs(context.rh_offset), axis=1))))
        + float(np.max(np.abs(draws.ln_frozen_multiplier[:, p])))
        + slope * float(np.max(context.d_t[p][indices]))
        + float(np.max(np.abs(draws.extrapolation_z_humidity[:, p])))
        * context.settings["extrapolation"]["ln_k_sd_per_percent_rh"]["value"]
        * float(np.max(context.d_rh[p][indices]))
    )
    if not math.isfinite(exponent_scale) or exponent_scale >= -math.log(float(np.finfo(np.float64).tiny)):
        return None
    log_rounding = (
        math.log(float(np.finfo(np.float64).eps))
        + math.log((2 * _SEGMENT_CHUNK + 32) * (1.0 + exponent_scale))
        + 2.0 * real_bound
    )
    if log_rounding >= math.log(tolerance):
        return None
    log_budget = math.log(tolerance - math.exp(log_rounding))
    rho = 2.0
    candidates = []
    while half * (rho + 1.0 / rho) / 2.0 < minimum:
        radius = half * (rho + 1.0 / rho) / 2.0
        complex_bound = a * radius / (minimum * (minimum - radius)) + slope * radius
        degree = max(
            1,
            math.ceil(
                (math.log(4.0) + complex_bound + real_bound - math.log(rho - 1.0) - log_budget)
                / math.log(rho)
            ),
        )
        candidates.append(degree)
        rho *= 2.0
    return min(candidates) if candidates else None


def _interpolation_weights(coordinates: FloatArray, nodes: FloatArray) -> FloatArray:
    if nodes.size == 1:
        return np.ones((coordinates.size, 1))
    weights = np.where(np.arange(nodes.size) % 2 == 0, 1.0, -1.0)
    weights[[0, -1]] *= 0.5
    difference = coordinates[:, None] - nodes[None, :]
    equal = difference == 0.0
    terms = weights[None, :] / np.where(equal, 1.0, difference)
    rows = np.any(equal, axis=1)
    terms[rows] = equal[rows].astype(np.float64)
    return np.asarray(terms / np.sum(terms, axis=1)[:, None], dtype=np.float64)


def _temperature_interpolation(context: _Context, p: int) -> _TemperatureInterpolation | None:
    path, table = context.model.pathways[p], context.table
    if path.driver != "thermal" or not table.tau.size:
        return None
    no_humidity = not has_humidity_term(path)
    if not no_humidity and not bool(np.all(np.isinf(table.tau_m) | (table.tau_m == 0.0))):
        return None
    rh_high = np.zeros_like(table.tau) if no_humidity else table.rh_in_high
    rh_low = np.zeros_like(table.tau) if no_humidity else table.rh_in_low
    canonical = dataclasses.replace(
        table,
        tau=np.ones_like(table.tau),
        tau_m=np.zeros_like(table.tau),
        rh_out_high=rh_high,
        rh_out_low=rh_low,
        rh_in_high=rh_high,
        rh_in_low=rh_low,
        rh_offset_mask=np.where(np.isinf(table.tau_m) | no_humidity, 0.0, table.rh_offset_mask),
        step=np.zeros_like(table.step),
    )
    minimum = _temperature_min(path, context.settings)
    branch = np.where(
        table.temperature < minimum,
        -1.0,
        np.where(table.temperature > path.validated_range.temperature_max_k, 1.0, 0.0),
    )
    keys = np.column_stack(
        (
            table.zone_index,
            table.t_offset_mask,
            canonical.rh_offset_mask,
            table.frozen,
            rh_high,
            rh_low,
            context.d_rh[p],
            branch,
        )
    )
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    groups = []
    total_nodes = 0
    for key in range(int(np.max(inverse)) + 1):
        indices = np.flatnonzero(inverse == key)
        lo, hi = float(np.min(table.temperature[indices])), float(np.max(table.temperature[indices]))
        degree = _interpolation_degree(context, p, indices, lo, hi)
        if degree is None:
            return None
        if degree + 1 >= indices.size:
            groups.append(_TemperatureGroup(_take_table(canonical, indices), indices, np.eye(indices.size)))
            total_nodes += indices.size
            continue
        nodes = np.cos(np.pi * np.arange(degree + 1) / degree) if degree else np.zeros(1)
        center, half = (hi + lo) / 2.0, (hi - lo) / 2.0
        node_table = _take_table(canonical, np.full(nodes.size, indices[0], dtype=np.intp))
        node_table = dataclasses.replace(node_table, temperature=center + half * nodes)
        coordinates = (table.temperature[indices] - center) / half if half else np.zeros(indices.size)
        groups.append(_TemperatureGroup(node_table, indices, _interpolation_weights(coordinates, nodes)))
        total_nodes += nodes.size
    if total_nodes > _SEGMENT_CHUNK:
        return None
    return _TemperatureInterpolation(tuple(groups), table.tau)


def _dictionary_accumulation(
    context: _Context, dictionaries: Sequence[_RateDictionary | _TemperatureInterpolation | None]
) -> _Accumulated:
    cached: list[FloatArray | tuple[FloatArray, ...] | None] = []
    for p, dictionary in enumerate(dictionaries):
        if dictionary is None:
            cached.append(None)
            continue
        if isinstance(dictionary, _TemperatureInterpolation):
            group_rates = []
            for group in dictionary.groups:
                ctx = _context(context.model, group.table, context.draws, context.settings)
                view = _grid_view(ctx, 0, group.table.tau.size)
                group_rates.append(_pathway_theta(ctx, view, p, view.tau))
            cached.append(tuple(group_rates))
            continue
        subcontext = _context(context.model, dictionary.table, context.draws, context.settings)

        # Widening distances belong to the original segment, not its canonical RH representative.
        def work(lo: int, p: int = p, ctx: _Context = subcontext) -> tuple[int, int, list[FloatArray]]:
            hi = min(ctx.table.tau.size, lo + _SEGMENT_CHUNK)
            view = _grid_view(ctx, lo, hi)
            return lo, hi, [_pathway_theta(ctx, view, p, view.tau)]

        cached.append(
            np.concatenate(
                [
                    parts[0]
                    for _, _, parts in _ordered_chunks(work, dictionary.table.tau.size, context.settings)
                ],
                axis=0,
            )
        )

    def provider(lo: int, hi: int) -> list[FloatArray]:
        view = _grid_view(context, lo, hi)
        chunks = []
        for p, (dictionary, values) in enumerate(zip(dictionaries, cached, strict=True)):
            if dictionary is None or values is None:
                chunks.append(_pathway_theta(context, view, p, view.tau))
            elif isinstance(dictionary, _TemperatureInterpolation):
                assert isinstance(values, tuple)
                chunk = np.empty((hi - lo, context.draws.n))
                for group, rates in zip(dictionary.groups, values, strict=True):
                    mask = (group.segments >= lo) & (group.segments < hi)
                    rows = group.segments[mask]
                    if rows.size:
                        chunk[rows - lo] = (group.weights[mask] @ rates) * dictionary.scale[rows, None]
                chunks.append(chunk)
            else:
                assert isinstance(values, np.ndarray)
                chunks.append(values[dictionary.inverse[lo:hi]] * dictionary.scale[lo:hi, None])
        return chunks

    return _accumulate(context, chunk_provider=provider)


def _probe_indices(draws: DrawBlock) -> NDArray[np.intp]:
    """Observed extrema and quartile ranks of every varied rate driver, in stable index order."""
    columns = [getattr(draws, name) for name in _DRAW_MATRICES]
    columns.extend(
        np.column_stack([mapping[key] for key in sorted(mapping)])
        for mapping in (draws.zone_ln_multiplier, draws.temperature_offset_k, draws.rh_offset_percent)
        if mapping
    )
    selected = {0, draws.n - 1}
    for matrix in columns:
        for column in matrix.T:
            order = np.argsort(column, kind="stable")
            for numerator in range(5):
                selected.add(int(order[(draws.n - 1) * numerator // 4]))
    return np.array(sorted(selected), dtype=np.intp)


def _probe_error(approximate: FloatArray, exact: FloatArray) -> float | None:
    positive = exact != 0.0
    if bool(np.any((~positive) & (approximate != 0.0))):
        return None
    if not bool(np.any(positive)):
        return 0.0
    return float(np.max(np.abs((approximate[positive] - exact[positive]) / exact[positive])))


def evaluate_draws_checked(
    model: StabilityModel,
    exposure: LotExposure,
    trace: MicroclimateTrace,
    step_responses: Mapping[str, Sequence[float]],
    draws: DrawBlock,
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    settings: EngineSettingsDoc,
) -> CheckedDrawResult:
    """Checked history compression with exact support probes and fallback (spec 12.5).

    Identical drivers share a rate evaluation. Eligible thermal rates can instead use
    temperature interpolation with an analytic bound over the complete batch support.
    Original segment order and reduction order are retained, including degradant attribution.
    See docs/design/COMPRESSION.md for the method and error analysis.
    """
    if draws.n <= 0:
        raise InputError("a DrawBlock needs at least one draw")
    if "compression_relative_tolerance" not in settings["numerics"]:
        raise ModelDomainError("checked evaluation requires compression_relative_tolerance")
    tolerance = settings["numerics"]["compression_relative_tolerance"]["value"]
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ModelDomainError("compression tolerance must be finite and non-negative")
    table = _segment_table(model, exposure, trace, step_responses, zones, packages)
    context = _context(model, table, draws, settings)
    dictionaries = tuple(
        _rate_dictionary(context, p) or _temperature_interpolation(context, p)
        for p in range(len(model.pathways))
    )
    if not any(d is not None for d in dictionaries):
        return CheckedDrawResult(_result(model, draws, _accumulate(context), settings), "exact", 0.0, False)
    probes = slice_draws(draws, _probe_indices(draws))
    probe_context = _context(model, table, probes, settings)
    exact = _result(model, probes, _accumulate(probe_context), settings)
    compressed = _result(model, probes, _dictionary_accumulation(probe_context, dictionaries), settings)
    theta_error = _probe_error(compressed.theta, exact.theta)
    degradant_error = _probe_error(compressed.degradant_percent, exact.degradant_percent)
    error = None if theta_error is None or degradant_error is None else max(theta_error, degradant_error)
    if error is None or error > tolerance:
        return CheckedDrawResult(_result(model, draws, _accumulate(context), settings), "exact", error, True)
    result = _result(model, draws, _dictionary_accumulation(context, dictionaries), settings)
    return CheckedDrawResult(result, "compressed", error, False)


def pathway_sums(
    model: StabilityModel, theta: FloatArray, settings: EngineSettingsDoc
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Per-draw sums of ``DrawResult.theta`` (shape (n, P)) over the pathways that enter
    alpha, over the racemization pathways and over the destructive radiolysis pathways, as
    evaluate_draws forms them."""
    values = np.asarray(theta, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != len(model.pathways):
        raise InputError(f"theta must have shape (n, {len(model.pathways)})")
    in_potency = np.array([_in_potency(pathway, settings) for pathway in model.pathways], dtype=np.bool_)
    racemization = np.array([pathway.role == "racemization" for pathway in model.pathways], dtype=np.bool_)
    radiation = np.array(
        [pathway.role == "destructive" and pathway.driver == "radiation" for pathway in model.pathways],
        dtype=np.bool_,
    )
    return (
        np.sum(np.where(in_potency[None, :], values, 0.0), axis=1),
        np.sum(np.where(racemization[None, :], values, 0.0), axis=1),
        np.sum(np.where(radiation[None, :], values, 0.0), axis=1),
    )


def potency_from_thetas(
    model: StabilityModel,
    c0_percent: FloatArray,
    theta_destructive: FloatArray,
    theta_racemization: FloatArray,
    settings: EngineSettingsDoc,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """(alpha, total potency C0 (1 - alpha), active potency C0 (1 - alpha) rho) from the
    summed Theta (spec 5.6, 5.7); arrays broadcast."""
    alpha = _conversion(model, np.asarray(theta_destructive, dtype=np.float64), settings)
    rho = active_fraction(np.asarray(theta_racemization, dtype=np.float64), model.initial_active_fraction)
    total = np.asarray(c0_percent, dtype=np.float64) * (1.0 - alpha)
    return alpha, total, total * rho


def _result(
    model: StabilityModel, draws: DrawBlock, accumulated: _Accumulated, settings: EngineSettingsDoc
) -> DrawResult:
    theta = accumulated.theta
    theta_destructive, theta_racemization, theta_radiation = pathway_sums(model, theta, settings)
    alpha, total, active = potency_from_thetas(
        model, draws.c0_percent, theta_destructive, theta_racemization, settings
    )
    rho = active_fraction(theta_racemization, model.initial_active_fraction)
    degradant = np.zeros_like(theta)
    for p, pathway in enumerate(model.pathways):
        spec = pathway.degradant
        if spec is not None and _in_potency(pathway, settings):
            # Molar basis: StabilityModel carries no API molar mass, so the mass ratio
            # M_degradant / M_API is applied by the pipeline (docs/requests/B1.md R-08).
            degradant[:, p] = draws.c0_percent * spec.yield_mol_per_mol * accumulated.degradant_alpha[:, p]
    result = DrawResult(
        theta=theta,
        alpha=alpha,
        active_fraction=rho,
        potency_total_percent=total,
        potency_active_percent=active,
        degradant_percent=degradant,
        radiation_loss_fraction=-np.expm1(-theta_radiation),
    )
    for name in (
        "theta",
        "alpha",
        "active_fraction",
        "potency_total_percent",
        "potency_active_percent",
        "degradant_percent",
        "radiation_loss_fraction",
    ):
        if not bool(np.all(np.isfinite(getattr(result, name)))):
            raise ComputationError(f"evaluate_draws produced a non-finite {name}")
    return result


def nominal_draw(
    model: StabilityModel, zones: Mapping[str, StorageZoneDoc], zone_ids: Sequence[str]
) -> DrawBlock:
    """The nominal draw of spec 5.8 as a one-draw DrawBlock: block means (Ea at its location
    mu), zone multipliers at their medians, offsets and z values 0, C0 = 100."""
    count = len(model.pathways)
    columns: dict[str, FloatArray] = {
        name: np.zeros((1, count), dtype=np.float64)
        for name in (
            "ln_k_ref_per_s",
            "ea_j_per_mol",
            "b_per_percent_rh",
            "ln_k_vis_per_lux_h",
            "ln_k_uv_per_w_h_per_m2",
            "ln_k_dose_per_gy",
            "ln_k_dose_frozen_per_gy",
            "ln_frozen_multiplier",
        )
    }
    target = {
        "ln_k_ref_per_s": "ln_k_ref_per_s",
        "ea_j_per_mol": "ea_j_per_mol",
        "b_per_percent_rh": "b_per_percent_rh",
        "ln_k_vis_per_lux_h": "ln_k_vis_per_lux_h",
        "ln_k_uv_per_w_h_per_m2": "ln_k_uv_per_w_h_per_m2",
        "ln_k_dose_per_gy": "ln_k_dose_per_gy",
        "ln_k_dose_frozen_per_gy": "ln_k_dose_frozen_per_gy",
        "ln_frozen_rate_multiplier": "ln_frozen_multiplier",
    }
    for p, pathway in enumerate(model.pathways):
        block = pathway.parameters
        for index, name in enumerate(block.names):
            columns[target[name]][0, p] = block.mean[index]
        if "ln_k_dose_frozen_per_gy" not in block.names:
            columns["ln_k_dose_frozen_per_gy"][0, p] = columns["ln_k_dose_per_gy"][0, p]
    zero = np.zeros(1, dtype=np.float64)
    multipliers: dict[str, FloatArray] = {}
    for zone_id in sorted(zone_ids):
        if zone_id not in zones:
            raise InputError(f"unknown zone {zone_id!r}")
        prior = zones[zone_id]["rate_multiplier"]
        multipliers[zone_id] = ln_rate_multiplier(prior["family"], prior["mu"], prior["sigma"], zero)
    return DrawBlock(
        n=1,
        c0_percent=np.full(1, 100.0),
        z_c0=zero.copy(),
        ln_k_ref_per_s=columns["ln_k_ref_per_s"],
        ea_j_per_mol=columns["ea_j_per_mol"],
        b_per_percent_rh=columns["b_per_percent_rh"],
        ln_k_vis_per_lux_h=columns["ln_k_vis_per_lux_h"],
        ln_k_uv_per_w_h_per_m2=columns["ln_k_uv_per_w_h_per_m2"],
        ln_k_dose_per_gy=columns["ln_k_dose_per_gy"],
        ln_k_dose_frozen_per_gy=columns["ln_k_dose_frozen_per_gy"],
        ln_frozen_multiplier=columns["ln_frozen_multiplier"],
        extrapolation_z_temperature=np.zeros((1, count), dtype=np.float64),
        extrapolation_z_humidity=np.zeros((1, count), dtype=np.float64),
        zone_ln_multiplier=multipliers,
        temperature_offset_k={zone_id: zero.copy() for zone_id in sorted(zone_ids)},
        rh_offset_percent={zone_id: zero.copy() for zone_id in sorted(zone_ids)},
    )


def nominal_thetas(
    model: StabilityModel,
    exposure: LotExposure,
    trace: MicroclimateTrace,
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    settings: EngineSettingsDoc,
) -> tuple[NominalPathwayTheta, ...]:
    """Theta per pathway at nominal parameters, used for contribution shares, the
    estimate basis (spec 9.6), the validated-range flag and placards (spec 8)."""
    zone_ids = sorted({segment.zone_id for segment in exposure.segments})
    # Offsets are 0 at the nominal draw, so the step responses never matter: zeros suffice.
    steps = {zone_id: np.zeros(len(exposure.segments)) for zone_id in zone_ids}
    table = _segment_table(model, exposure, trace, {k: tuple(v) for k, v in steps.items()}, zones, packages)
    draws = nominal_draw(model, zones, zone_ids)
    context = _context(model, table, draws, settings)
    accumulated = _accumulate(context, keep_segments=True)
    if accumulated.segment_theta is None:  # pragma: no cover - keep_segments=True always keeps them
        raise ComputationError("segment detail missing")
    rh_in_max = np.maximum(table.rh_in_high, table.rh_in_end_high)
    out: list[NominalPathwayTheta] = []
    for p, pathway in enumerate(model.pathways):
        per_segment = accumulated.segment_theta[p][:, 0]
        outside = (context.d_t[p] > 0.0) | (context.d_rh[p] > 0.0)
        out.append(
            NominalPathwayTheta(
                pathway_id=pathway.pathway_id,
                theta=float(accumulated.theta[0, p]),
                theta_out_of_range=float(np.sum(np.where(outside, per_segment, 0.0))),
                theta_frozen=float(np.sum(np.where(table.frozen, per_segment, 0.0))),
                theta_phase_change=float(np.sum(np.where(table.phase_change, per_segment, 0.0))),
                segment_theta=tuple(float(v) for v in per_segment),
                segment_d_rh_percent=tuple(float(v) for v in context.d_rh[p]),
                segment_rh_in_max_percent=tuple(float(v) for v in rh_in_max),
            )
        )
    return tuple(out)


# =========================================================================================
# Shares, validated range and placards (spec 8, 9.6)
# =========================================================================================


def _nominal_by_pathway(
    model: StabilityModel, nominal: Sequence[NominalPathwayTheta]
) -> list[NominalPathwayTheta]:
    by_id = {entry.pathway_id: entry for entry in nominal}
    missing = [pathway.pathway_id for pathway in model.pathways if pathway.pathway_id not in by_id]
    if missing:
        raise InputError(f"nominal thetas missing for pathways {missing}")
    return [by_id[pathway.pathway_id] for pathway in model.pathways]


def nominal_shares(
    model: StabilityModel, nominal: Sequence[NominalPathwayTheta], settings: EngineSettingsDoc
) -> tuple[float, ...]:
    """Nominal share of each pathway in model order (spec 9.6): potency-relevant
    destructive pathways share their nominal Theta; for racemizing ingredients the
    racemization share is s_r = (1 - rho/f0) / (1 - (1 - alpha) rho/f0) and the destructive
    shares are scaled by (1 - s_r). Radiolysis outside potency has share 0."""
    entries = _nominal_by_pathway(model, nominal)
    count = len(model.pathways)
    theta = [entry.theta for entry in entries]
    in_potency = [_in_potency(pathway, settings) for pathway in model.pathways]
    racemization = [p for p in range(count) if model.pathways[p].role == "racemization"]
    theta_destructive = math.fsum(theta[p] for p in range(count) if in_potency[p])
    theta_racemization = math.fsum(theta[p] for p in racemization)
    shares = [0.0] * count
    racemization_share = 0.0
    if racemization:
        alpha = float(_conversion(model, np.array([theta_destructive]), settings)[0])
        f0 = model.initial_active_fraction
        rho = float(active_fraction(np.array([theta_racemization]), f0)[0])
        denominator = 1.0 - (1.0 - alpha) * rho / f0
        if denominator > 0.0:
            racemization_share = min(1.0, max(0.0, (1.0 - rho / f0) / denominator))
        if theta_racemization > 0.0:
            for p in racemization:
                shares[p] = racemization_share * theta[p] / theta_racemization
    if theta_destructive > 0.0:
        for p in range(count):
            if in_potency[p]:
                shares[p] = (1.0 - racemization_share) * theta[p] / theta_destructive
    return tuple(shares)


def contributing_pathways(
    model: StabilityModel, nominal: Sequence[NominalPathwayTheta], settings: EngineSettingsDoc
) -> tuple[bool, ...]:
    """A pathway contributes when its nominal share is at least ``basis.share_threshold``;
    if none does (empty history), every pathway counts (spec 9.6)."""
    threshold = float(settings["basis"]["share_threshold"]["value"])
    flags = tuple(share >= threshold for share in nominal_shares(model, nominal, settings))
    if not any(flags):
        return tuple(True for _ in flags)
    return flags


@dataclasses.dataclass(frozen=True, slots=True)
class RangeAssessment:
    """Validated-range and regime findings at nominal values (spec 8.1 to 8.4).
    ``share_outside`` is the share-weighted fraction of nominal Theta accrued in segments
    outside a pathway's validated range (docs/requests/B1.md R-09)."""

    share_outside: float
    frozen: bool
    possibly_frozen: bool
    phase_change: bool
    deliquescence: bool
    time_extrapolation_ratio: float | None
    time_extrapolated: bool
    outside_validated_range: bool
    regime_switch: bool
    placards: tuple[Placard, ...]


def _temperature_lower_bounds(exposure: LotExposure) -> FloatArray:
    """Lower temperature bound of each segment: the gap record's low fill in temperature
    gaps (spec 3.5, used only for the freezing check of 8.3), else the segment value."""
    records = [
        gap for history in exposure.zone_histories for gap in history.gaps if gap.quantity == "temperature_k"
    ]
    out = np.empty(len(exposure.segments), dtype=np.float64)
    for index, segment in enumerate(exposure.segments):
        out[index] = segment.temperature_k
        if "temperature_k" not in segment.gap_quantities:
            continue
        matches = [
            gap
            for gap in records
            if gap.zone_id == segment.zone_id and gap.t0_s <= segment.t0_s and segment.t1_s <= gap.t1_s
        ]
        if not matches:
            raise InputError(f"segment {index} is temperature gap-filled but has no gap record")
        low = matches[0].fill_value_low
        out[index] = segment.temperature_k if low is None else low
    return out


def assess_validated_range(
    model: StabilityModel,
    exposure: LotExposure,
    nominal: Sequence[NominalPathwayTheta],
    settings: EngineSettingsDoc,
) -> RangeAssessment:
    """Flags ``outside_validated_range`` and ``regime_switch`` and the first five placards
    of spec 8.4, from nominal Theta. Shares are weighted by the nominal contribution share
    of each pathway (spec 9.6), so the racemization share counts like a destructive one."""
    entries = _nominal_by_pathway(model, nominal)
    segments = exposure.segments
    count = len(segments)
    for entry in entries:
        lengths = {
            len(entry.segment_theta),
            len(entry.segment_d_rh_percent),
            len(entry.segment_rh_in_max_percent),
        }
        if lengths != {count}:
            raise InputError(
                f"nominal thetas of {entry.pathway_id!r} lack per-segment detail; "
                "build them with nominal_thetas"
            )
    shares = nominal_shares(model, nominal, settings)
    contributing = contributing_pathways(model, nominal, settings)
    weights = [shares[p] / entries[p].theta if entries[p].theta > 0.0 else 0.0 for p in range(len(entries))]
    tau = np.array([s.t1_s - s.t0_s for s in segments], dtype=np.float64)
    temperature = np.array([s.temperature_k for s in segments], dtype=np.float64)
    segment_theta = [np.array(entry.segment_theta, dtype=np.float64) for entry in entries]
    d_rh = [np.array(entry.segment_d_rh_percent, dtype=np.float64) for entry in entries]
    rh_in_max = (
        np.array(entries[0].segment_rh_in_max_percent, dtype=np.float64) if entries else np.zeros(count)
    )
    d_t = []
    for pathway in model.pathways:
        validated = pathway.validated_range
        d_t.append(
            np.maximum(
                0.0,
                np.maximum(
                    temperature - validated.temperature_max_k,
                    _temperature_min(pathway, settings) - temperature,
                ),
            )
        )

    def weighted_share(masks: Sequence[BoolArray]) -> float:
        return math.fsum(
            weights[p] * float(np.sum(np.where(masks[p], segment_theta[p], 0.0))) for p in range(len(entries))
        )

    def weighted_fraction(fraction: FloatArray) -> float:
        return math.fsum(weights[p] * float(np.sum(segment_theta[p] * fraction)) for p in range(len(entries)))

    liquid = model.dose_form_family in _LIQUID_FAMILIES
    freezing = model.freezing_point_k
    empty = np.zeros(count, dtype=np.bool_)
    frozen = temperature < freezing if liquid and freezing is not None else empty
    possibly_frozen = empty
    lower_bounds = temperature
    if liquid and freezing is not None:
        lower_bounds = _temperature_lower_bounds(exposure)
        possibly_frozen = ~frozen & (lower_bounds < freezing)
    phase_threshold = model.phase_change_temperature_k
    phase = temperature > phase_threshold if phase_threshold is not None else empty
    deliquescence_threshold = model.deliquescence_rh_percent
    deliquescent = rh_in_max > deliquescence_threshold if deliquescence_threshold is not None else empty

    placards: list[Placard] = []
    t_masks = [d_t[p] > 0.0 for p in range(len(entries))]
    t_union = np.zeros(count, dtype=np.bool_)
    t_extent = 0.0
    for p, flag in enumerate(contributing):
        if flag and bool(np.any(t_masks[p])):
            t_union |= t_masks[p]
            t_extent = max(t_extent, float(np.max(d_t[p])))
    if bool(np.any(t_union)):
        placards.append(
            Placard(
                kind="temperature_range",
                extent=t_extent,
                unit="K",
                duration_s=float(np.sum(tau[t_union])),
                share_nominal=weighted_share([t_masks[p] & contributing[p] for p in range(len(entries))]),
                message=(
                    f"Rates up to {t_extent:.2f} K outside the validated temperature range are extrapolated; "
                    "the interval is widened."
                ),
            )
        )
    h_masks = [(d_rh[p] > 0.0) | deliquescent for p in range(len(entries))]
    h_union = deliquescent.copy()
    h_extent = (
        float(np.max(rh_in_max[deliquescent] - deliquescence_threshold))
        if deliquescence_threshold is not None and bool(np.any(deliquescent))
        else 0.0
    )
    for p, flag in enumerate(contributing):
        if flag and bool(np.any(d_rh[p] > 0.0)):
            h_union |= d_rh[p] > 0.0
            h_extent = max(h_extent, float(np.max(d_rh[p])))
    if bool(np.any(h_union)):
        placards.append(
            Placard(
                kind="humidity_range",
                extent=h_extent,
                unit="%RH",
                duration_s=float(np.sum(tau[h_union])),
                share_nominal=weighted_share([h_masks[p] & contributing[p] for p in range(len(entries))]),
                message=(
                    f"Rates up to {h_extent:.2f} %RH outside the validated humidity range or above the "
                    "deliquescence threshold are extrapolated; the interval is widened."
                ),
            )
        )
    ratio: float | None = None
    time_extrapolated = False
    limit = float(settings["extrapolation"]["max_time_extrapolation_ratio"]["value"])
    if model.measured_model_id is not None and model.max_study_duration_s is not None:
        if not model.max_study_duration_s > 0.0:
            raise ModelDomainError("max_study_duration_s must be positive")
        age = exposure.t_end_s - exposure.t_start_s
        ratio = age / model.max_study_duration_s
        if ratio > limit:
            time_extrapolated = True
            cutoff = exposure.t_start_s + limit * model.max_study_duration_s
            starts = np.array([s.t0_s for s in segments], dtype=np.float64)
            ends = np.array([s.t1_s for s in segments], dtype=np.float64)
            # Time share of each segment past the cutoff (Theta taken uniform in time
            # inside a straddling segment, for display).
            fraction = np.clip((ends - np.maximum(starts, cutoff)) / np.where(tau > 0.0, tau, 1.0), 0.0, 1.0)
            placards.append(
                Placard(
                    kind="time_extrapolation",
                    extent=ratio,
                    unit="1",
                    duration_s=age - limit * model.max_study_duration_s,
                    share_nominal=weighted_fraction(fraction),
                    message=(
                        f"The lot age is {ratio:.2f} times the longest stability study time point, "
                        f"beyond the allowed {limit:g}; potency is extrapolated in time."
                    ),
                )
            )
    cold = frozen | possibly_frozen
    if bool(np.any(cold)) and freezing is not None:
        coldest = float(np.min(np.where(frozen, temperature, lower_bounds)[cold]))
        extent = freezing - coldest
        placards.append(
            Placard(
                kind="regime_frozen",
                extent=extent,
                unit="K",
                duration_s=float(np.sum(tau[cold])),
                share_nominal=weighted_share([cold] * len(entries)),
                message=(
                    f"The product was frozen or possibly frozen, down to {extent:.2f} K below its freezing "
                    "point; the frozen-state rate is uncertain."
                ),
            )
        )
    if bool(np.any(phase)) and phase_threshold is not None:
        extent = float(np.max(temperature[phase])) - phase_threshold
        placards.append(
            Placard(
                kind="regime_phase_change",
                extent=extent,
                unit="K",
                duration_s=float(np.sum(tau[phase])),
                share_nominal=weighted_share([phase] * len(entries)),
                message=(
                    f"The product exceeded its phase-change temperature by up to {extent:.2f} K; the rate "
                    "model may not hold."
                ),
            )
        )
    share_outside = math.fsum(weights[p] * entries[p].theta_out_of_range for p in range(len(entries)))
    flag_threshold = float(settings["extrapolation"]["flag_share_threshold"]["value"])
    any_frozen = bool(np.any(frozen))
    any_phase = bool(np.any(phase))
    any_deliquescent = bool(np.any(deliquescent))
    return RangeAssessment(
        share_outside=share_outside,
        frozen=any_frozen,
        possibly_frozen=bool(np.any(possibly_frozen)),
        phase_change=any_phase,
        deliquescence=any_deliquescent,
        time_extrapolation_ratio=ratio,
        time_extrapolated=time_extrapolated,
        outside_validated_range=(
            share_outside >= flag_threshold
            or any_frozen
            or any_phase
            or any_deliquescent
            or time_extrapolated
        ),
        regime_switch=any_frozen or bool(np.any(possibly_frozen)) or any_phase or any_deliquescent,
        placards=tuple(
            dataclasses.replace(p, share_nominal=min(1.0, max(0.0, p.share_nominal))) for p in placards
        ),
    )


def placards_for(
    model: StabilityModel,
    exposure: LotExposure,
    nominal: Sequence[NominalPathwayTheta],
    settings: EngineSettingsDoc,
) -> tuple[Placard, ...]:
    """NASA-STD-7009B [M&S 26] placards (spec 8.4): one per limit type exceeded, stating
    the extent (K or %RH beyond the range, extrapolation ratio), time spent and the share of
    nominal Theta accrued there."""
    return assess_validated_range(model, exposure, nominal, settings).placards

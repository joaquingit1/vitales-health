"""Oracle for spec section 5 (kinetics) at 50 significant digits.

Written from docs/ENGINE-SPEC.md 5.1 to 5.4 and table 5.3.1 only. Every function takes
floats (converted exactly) and returns an mpmath number.
"""

from collections.abc import Sequence

from pharmacy.types import RateLawKind
from tests.oracle.precision import MP, R_MP, Mpf

FINITE_COMPLETION: tuple[RateLawKind, ...] = ("F0", "R2", "R3", "D1", "D2", "D3", "D4")
"""Kinds whose conversion reaches 1 at a finite Theta (plus Fn with n < 1)."""

ALL_KINDS: tuple[RateLawKind, ...] = (
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


class OracleDomainError(ValueError):
    """Inputs outside a kind's domain (the engine must raise ModelDomainError there)."""


def rate_constant(
    ln_k_ref_per_s: float,
    ea_j_per_mol: float,
    b_per_percent_rh: float,
    temperature_k: float,
    rh_percent: float,
    reference_temperature_k: float,
    reference_rh_percent: float,
    gas_constant: Mpf = R_MP,
) -> Mpf:
    """ln k = ln k* - (Ea/R)(1/T - 1/T*) + B (RH - RH*) (spec 5.1)."""
    t = MP.mpf(temperature_k)
    t_ref = MP.mpf(reference_temperature_k)
    if t <= 0 or t_ref <= 0:
        raise OracleDomainError("temperatures must be positive")
    exponent = (
        MP.mpf(ln_k_ref_per_s)
        - MP.mpf(ea_j_per_mol) / MP.mpf(gas_constant) * (1 / t - 1 / t_ref)
        + MP.mpf(b_per_percent_rh) * (MP.mpf(rh_percent) - MP.mpf(reference_rh_percent))
    )
    return MP.exp(exponent)


def integrated_rate(
    ln_k_ref_per_s: float,
    ea_j_per_mol: float,
    b_per_percent_rh: float,
    temperatures_k: Sequence[float],
    rh_percent: Sequence[float],
    durations_s: Sequence[float],
    reference_temperature_k: float,
    reference_rh_percent: float,
    gas_constant: Mpf = R_MP,
) -> Mpf:
    """Theta = sum_i k(T_i, RH_i) tau_i (spec 5.2)."""
    if not len(temperatures_k) == len(rh_percent) == len(durations_s):
        raise OracleDomainError("mismatched lengths")
    total = MP.mpf(0)
    for t, rh, tau in zip(temperatures_k, rh_percent, durations_s, strict=True):
        if tau < 0:
            raise OracleDomainError("negative duration")
        total += rate_constant(
            ln_k_ref_per_s,
            ea_j_per_mol,
            b_per_percent_rh,
            t,
            rh,
            reference_temperature_k,
            reference_rh_percent,
            gas_constant,
        ) * MP.mpf(tau)
    return total


# =========================================================================================
# Table 5.3.1
# =========================================================================================


def _avrami_m(kind: RateLawKind, avrami_n: float | None) -> Mpf:
    if kind == "A2":
        return MP.mpf(2)
    if kind == "A3":
        return MP.mpf(3)
    if kind == "A4":
        return MP.mpf(4)
    if avrami_n is None or not avrami_n > 0:
        raise OracleDomainError("An needs avrami_n > 0")
    return MP.mpf(avrami_n)


def _order(order_n: float | None) -> Mpf:
    if order_n is None or not order_n > 0 or order_n == 1:
        raise OracleDomainError("Fn needs order_n > 0 and != 1")
    return MP.mpf(order_n)


def _seed(alpha0: float | None) -> Mpf:
    if alpha0 is None or not 0 < alpha0 < 1:
        raise OracleDomainError("B1 needs alpha0 in (0, 1)")
    return MP.mpf(alpha0)


def _logit(x: Mpf) -> Mpf:
    return MP.log(x / (1 - x))


def completion_theta(
    kind: RateLawKind,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> Mpf:
    """Theta at which alpha reaches 1 (table 5.3.1 "Complete at"), +inf for asymptotic kinds."""
    if kind == "D4":
        return MP.mpf(1) / 3
    if kind in FINITE_COMPLETION:
        return MP.mpf(1)
    if kind == "Fn":
        n = _order(order_n)
        return 1 / (1 - n) if n < 1 else MP.inf
    if kind == "An":
        _avrami_m(kind, avrami_n)
    if kind == "B1":
        _seed(alpha0)
    return MP.inf


def theta_from_conversion(
    kind: RateLawKind,
    alpha: float | Mpf,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> Mpf:
    """g(alpha), the integral form (table 5.3.1)."""
    a = MP.mpf(alpha)
    if a < 0 or a > 1:
        raise OracleDomainError("alpha must lie in [0, 1]")
    one_minus = 1 - a
    if a == 1 and kind not in (*FINITE_COMPLETION, "Fn"):
        raise OracleDomainError(f"{kind} never completes")
    if kind == "F0":
        return a
    if kind == "F1":
        return -MP.log(one_minus)
    if kind == "F2":
        return a / one_minus
    if kind == "Fn":
        n = _order(order_n)
        if a == 1 and n >= 1:
            raise OracleDomainError("Fn with n > 1 never completes")
        return (one_minus ** (1 - n) - 1) / (n - 1)
    if kind == "R2":
        return 1 - MP.sqrt(one_minus)
    if kind == "R3":
        return 1 - MP.cbrt(one_minus)
    if kind in ("A2", "A3", "A4", "An"):
        m = _avrami_m(kind, avrami_n)
        return (-MP.log(one_minus)) ** (1 / m)
    if kind == "B1":
        a0 = _seed(alpha0)
        return _logit(a0 + (1 - a0) * a) - _logit(a0)
    if kind == "D1":
        return a * a
    if kind == "D2":
        return one_minus * MP.log(one_minus) + a if a < 1 else MP.mpf(1)
    if kind == "D3":
        return (1 - MP.cbrt(one_minus)) ** 2
    if kind == "D4":
        return 1 - 2 * a / 3 - one_minus ** (MP.mpf(2) / 3)
    raise OracleDomainError(f"unknown kind {kind!r}")


def _bisect_inverse(kind: RateLawKind, theta: Mpf) -> Mpf:
    """Solve g(alpha) = theta on [0, 1) for D2 and D4 (g increasing) to full precision."""
    with MP.workdps(MP.dps + 30):
        lo, hi = MP.mpf(0), MP.mpf(1)
        target = MP.mpf(theta)
        for _ in range(4 * (MP.prec + 10)):
            mid = (lo + hi) / 2
            if theta_from_conversion(kind, mid) < target:
                lo = mid
            else:
                hi = mid
            if hi - lo <= MP.eps * hi:
                break
        return (lo + hi) / 2


def conversion_from_theta(
    kind: RateLawKind,
    theta: float | Mpf,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> Mpf:
    """alpha = G(Theta) (table 5.3.1); G = 1 beyond completion."""
    th = MP.mpf(theta)
    if th < 0:
        raise OracleDomainError("Theta must be non-negative")
    complete = completion_theta(kind, order_n=order_n, alpha0=alpha0, avrami_n=avrami_n)
    if th >= complete:
        return MP.mpf(1)
    if kind == "F0":
        return th
    if kind == "F1":
        return -MP.expm1(-th)
    if kind == "F2":
        return th / (1 + th)
    if kind == "Fn":
        n = _order(order_n)
        return 1 - (1 + (n - 1) * th) ** (1 / (1 - n))
    if kind == "R2":
        return th * (2 - th)
    if kind == "R3":
        return th * (3 - 3 * th + th * th)
    if kind in ("A2", "A3", "A4", "An"):
        m = _avrami_m(kind, avrami_n)
        return -MP.expm1(-(th**m))
    if kind == "B1":
        a0 = _seed(alpha0)
        a = 1 / (1 + (1 - a0) / a0 * MP.exp(-th))
        return (a - a0) / (1 - a0)
    if kind == "D1":
        return MP.sqrt(th)
    if kind == "D3":
        s = MP.sqrt(th)
        return s * (3 - 3 * s + s * s)
    if kind in ("D2", "D4"):
        return _bisect_inverse(kind, th) if th > 0 else MP.mpf(0)
    raise OracleDomainError(f"unknown kind {kind!r}")


def rate_function(
    kind: RateLawKind,
    alpha: float | Mpf,
    *,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> Mpf:
    """f(alpha) of d(alpha)/dt = k f(alpha) (table 5.3.1)."""
    a = MP.mpf(alpha)
    if a < 0 or a >= 1:
        raise OracleDomainError("alpha must lie in [0, 1)")
    one_minus = 1 - a
    if kind == "F0":
        return MP.mpf(1)
    if kind == "F1":
        return one_minus
    if kind == "F2":
        return one_minus**2
    if kind == "Fn":
        return one_minus ** _order(order_n)
    if kind == "R2":
        return 2 * MP.sqrt(one_minus)
    if kind == "R3":
        return 3 * one_minus ** (MP.mpf(2) / 3)
    if kind in ("A2", "A3", "A4", "An"):
        m = _avrami_m(kind, avrami_n)
        return m * one_minus * (-MP.log(one_minus)) ** ((m - 1) / m)
    if kind == "B1":
        a0 = _seed(alpha0)
        raw = a0 + (1 - a0) * a
        return raw * (1 - raw) / (1 - a0)
    if kind == "D1":
        return 1 / (2 * a)
    if kind == "D2":
        return 1 / (-MP.log1p(-a))
    if kind == "D3":
        return MP.mpf(3) / 2 * one_minus ** (MP.mpf(2) / 3) / (1 - MP.cbrt(one_minus))
    if kind == "D4":
        return MP.mpf(3) / 2 / (one_minus ** (-MP.mpf(1) / 3) - 1)
    raise OracleDomainError(f"unknown kind {kind!r}")


# =========================================================================================
# Humidity integral (spec 5.4)
# =========================================================================================


def humidity_integral(x: float | Mpf, duration_s: float | Mpf, tau_m_s: float | Mpf) -> Mpf:
    """J(x, D, tau_m) = integral_0^D exp(x e^(-t/tau_m)) dt, by the exact antiderivative
    tau_m (Ei(x) - Ei(x e^(-D/tau_m))); x = 0 gives D, tau_m = inf gives D e^x and
    tau_m = 0 gives D (spec 5.4)."""
    xx, d, tau = MP.mpf(x), MP.mpf(duration_s), MP.mpf(tau_m_s)
    if d < 0 or tau < 0:
        raise OracleDomainError("duration and tau_m must be non-negative")
    if d == 0:
        return MP.mpf(0)
    if MP.isinf(tau):
        return d * MP.exp(xx)
    if tau == 0 or xx == 0:
        return d
    with MP.workdps(MP.dps + 40):
        return tau * (MP.ei(xx) - MP.ei(xx * MP.exp(-d / tau)))


def humidity_integral_quadrature(x: float, duration_s: float, tau_m_s: float) -> Mpf:
    """Independent check of ``humidity_integral`` by adaptive Gauss-Legendre quadrature."""
    xx, d, tau = MP.mpf(x), MP.mpf(duration_s), MP.mpf(tau_m_s)
    if d == 0 or xx == 0 or tau == 0:
        return d
    # split at tau x 2^k so every piece is smooth and short relative to its own scale
    nodes = [MP.mpf(0)]
    edge = min(tau, d)
    while edge < d:
        nodes.append(edge)
        edge *= 2
    nodes.append(d)
    return MP.quad(lambda t: MP.exp(xx * MP.exp(-t / tau)), nodes)

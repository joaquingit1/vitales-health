"""True potency of a simulated lot for the virtual stability studies (spec 15.8).

Theta = integral over time of sum_p k_p(T(t), RH_in(t)) dt, from the simulator's truth series
(never sensor readings), by the trapezoid rule with compensated summation (``math.fsum``)
on the simulator grid, refined by step halving (linear interpolation of the truth between
grid nodes) until two successive refinements differ by less than the target. The halving
difference is reported with the result. Internal RH comes from the oracle's own package
model (spec 4.2, restated here): dRH_in/dt = (RH_out - RH_in)/tau_m(T), integrated exactly
for linear RH_out over each sub-step with tau_m at the sub-step's mean temperature.

The same code runs in float (``math``) for every replicate and in mpmath at 50 digits for
the spot checks; the arithmetic backend is passed in. Light and radiation are not part of
the truth (virtual chambers are dark and radiation is reported, not in potency, spec 6.1).
"""

import dataclasses
import math
from collections.abc import Callable, Sequence
from typing import Any

from pharmacy.types import PackageDoc
from tests.oracle.precision import MP, R_FLOAT, R_MP, SECONDS_PER_DAY

DEFAULT_RELATIVE_TARGET = 1e-6
DEFAULT_MAX_HALVINGS = 10


@dataclasses.dataclass(frozen=True, slots=True)
class TruthPathway:
    """One thermal pathway of the true chemistry (one draw from the engine's prior)."""

    ln_k_ref_per_s: float
    ea_j_per_mol: float
    b_per_percent_rh: float
    z_temperature: float = 0.0
    z_humidity: float = 0.0
    temperature_min_k: float = -math.inf
    temperature_max_k: float = math.inf
    rh_min_percent: float = -math.inf
    rh_max_percent: float = math.inf
    humidity_term: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class TruthModel:
    pathways: tuple[TruthPathway, ...]
    reference_temperature_k: float
    reference_rh_percent: float
    sd_ln_k_per_kelvin: float = 0.0
    sd_ln_k_per_percent_rh: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class PackageModel:
    """Restated package moisture model: kind, sealed RH, C_eff, P at T_test and E_perm."""

    kind: str
    sealed_rh_percent: float | None
    capacity_mg_per_percent_rh: float | None
    permeance_test_mg_per_s_per_percent_rh: float | None
    test_temperature_k: float | None
    permeation_energy_j_per_mol: float | None
    initial_rh_percent: float | None

    @staticmethod
    def open_dish() -> PackageModel:
        """Internal RH equals chamber RH (isoconversion design, spec 15.8)."""
        return PackageModel("none", None, None, None, None, None, None)

    @staticmethod
    def from_doc(package: PackageDoc) -> PackageModel:
        m = package["moisture_model"]
        kind = m["kind"]
        initial = m["initial_internal_rh_percent"]
        if kind == "none":
            return PackageModel("none", None, None, None, None, None, None)
        if kind == "sealed_constant":
            sealed = m["sealed_rh_percent"]
            assert sealed is not None
            return PackageModel("sealed_constant", sealed["value"], None, None, None, None, None)
        headspace = m["headspace_capacity_mg_per_percent_rh"]
        capacity = (0.0 if headspace is None else headspace["value"]) + math.fsum(
            c["dry_mass_g"]["value"] * c["sorption_slope_mg_per_g_per_percent_rh"]["value"]
            for c in m["contents"]
        )
        mvtr, d_rh, t_test, e_perm = (
            m["mvtr_mg_per_day"],
            m["mvtr_test_delta_rh_percent"],
            m["mvtr_test_temperature_k"],
            m["permeation_activation_energy_j_per_mol"],
        )
        assert mvtr is not None
        assert d_rh is not None
        assert t_test is not None
        assert e_perm is not None
        return PackageModel(
            "first_order_exchange",
            None,
            capacity,
            mvtr["value"] / d_rh["value"] / SECONDS_PER_DAY,
            t_test["value"],
            e_perm["value"],
            None if initial is None else initial["value"],
        )


@dataclasses.dataclass(frozen=True, slots=True)
class TruthIntegral:
    """Cumulative Theta at each requested grid index, the final RH_in, the halving
    difference (relative, at the last requested index) and the sub-steps per interval."""

    theta_at: tuple[float, ...]
    rh_in_end: float
    halving_relative_error: float
    substeps: int


@dataclasses.dataclass(frozen=True, slots=True)
class _Backend:
    num: Callable[[float], Any]
    exp: Callable[[Any], Any]
    fsum: Callable[[Sequence[Any]], Any]
    gas_constant: Any


FLOAT_BACKEND = _Backend(float, math.exp, math.fsum, R_FLOAT)
MP_BACKEND = _Backend(MP.mpf, MP.exp, MP.fsum, R_MP)


def _tau_m(package: PackageModel, temperature: Any, bk: _Backend) -> Any:
    assert package.capacity_mg_per_percent_rh is not None
    assert package.permeance_test_mg_per_s_per_percent_rh is not None
    assert package.test_temperature_k is not None
    assert package.permeation_energy_j_per_mol is not None
    p = bk.num(package.permeance_test_mg_per_s_per_percent_rh) * bk.exp(
        -(bk.num(package.permeation_energy_j_per_mol) / bk.gas_constant)
        * (1 / temperature - 1 / bk.num(package.test_temperature_k))
    )
    return bk.num(package.capacity_mg_per_percent_rh) / p


def _rate(model: TruthModel, temperature: Any, rh_in: Any, ln_multiplier: Any, bk: _Backend) -> Any:
    total = []
    t_ref = bk.num(model.reference_temperature_k)
    rh_ref = bk.num(model.reference_rh_percent)
    for p in model.pathways:
        ln_k = bk.num(p.ln_k_ref_per_s) + ln_multiplier
        ln_k -= bk.num(p.ea_j_per_mol) / bk.gas_constant * (1 / temperature - 1 / t_ref)
        if p.humidity_term:
            ln_k += bk.num(p.b_per_percent_rh) * (rh_in - rh_ref)
        if p.z_temperature != 0.0:
            d_t = max(0, temperature - bk.num(p.temperature_max_k), bk.num(p.temperature_min_k) - temperature)
            ln_k += bk.num(p.z_temperature) * bk.num(model.sd_ln_k_per_kelvin) * d_t
        if p.z_humidity != 0.0 and p.humidity_term:
            d_rh = max(0, rh_in - bk.num(p.rh_max_percent), bk.num(p.rh_min_percent) - rh_in)
            ln_k += bk.num(p.z_humidity) * bk.num(model.sd_ln_k_per_percent_rh) * d_rh
        total.append(bk.exp(ln_k))
    return bk.fsum(total)


def _relax_linear(rh_in: Any, u0: Any, u1: Any, h: Any, tau: Any, bk: _Backend) -> Any:
    """Exact R(h) of dR/dt = (u(t) - R)/tau with u linear from u0 to u1 over [0, h]."""
    slope = (u1 - u0) / h
    decay = bk.exp(-h / tau)
    return u1 - slope * tau + (rh_in - u0 + slope * tau) * decay


def _integrate_once(
    model: TruthModel,
    times_s: Sequence[float],
    temperature_k: Sequence[float],
    rh_out_percent: Sequence[float],
    package: PackageModel,
    ln_multiplier: float,
    report_indices: Sequence[int],
    substeps: int,
    rh_in_start: float | None,
    bk: _Backend,
) -> tuple[list[Any], Any]:
    n = len(times_s)
    m = substeps
    ln_m = bk.num(ln_multiplier)
    if package.kind == "sealed_constant":
        assert package.sealed_rh_percent is not None
        rh_in = bk.num(package.sealed_rh_percent)
    elif package.kind == "none":
        rh_in = bk.num(rh_out_percent[0])
    elif rh_in_start is not None:
        rh_in = bk.num(rh_in_start)
    elif package.initial_rh_percent is not None:
        rh_in = bk.num(package.initial_rh_percent)
    else:
        rh_in = bk.num(rh_out_percent[0])
    report = set(report_indices)
    out: list[Any] = []
    pieces: list[Any] = []
    k_prev = _rate(model, bk.num(temperature_k[0]), rh_in, ln_m, bk)
    if 0 in report:
        out.append(bk.num(0))
    for i in range(1, n):
        t0, t1 = bk.num(times_s[i - 1]), bk.num(times_s[i])
        a0, a1 = bk.num(temperature_k[i - 1]), bk.num(temperature_k[i])
        u0, u1 = bk.num(rh_out_percent[i - 1]), bk.num(rh_out_percent[i])
        h = (t1 - t0) / m
        for j in range(1, m + 1):
            frac_prev, frac = bk.num(j - 1) / m, bk.num(j) / m
            temp = a0 + (a1 - a0) * frac
            u_prev = u0 + (u1 - u0) * frac_prev
            u_now = u0 + (u1 - u0) * frac
            if package.kind == "none":
                rh_in = u_now
            elif package.kind == "first_order_exchange":
                temp_mid = a0 + (a1 - a0) * (frac_prev + frac) / 2
                rh_in = _relax_linear(rh_in, u_prev, u_now, h, _tau_m(package, temp_mid, bk), bk)
            k_now = _rate(model, temp, rh_in, ln_m, bk)
            pieces.append(h * (k_prev + k_now) / 2)
            k_prev = k_now
        if i in report:
            out.append(bk.fsum(pieces))
    return out, rh_in


def integrate_truth(
    model: TruthModel,
    times_s: Sequence[float],
    temperature_k: Sequence[float],
    rh_out_percent: Sequence[float],
    package: PackageModel,
    *,
    ln_multiplier: float = 0.0,
    report_indices: Sequence[int] | None = None,
    rh_in_start: float | None = None,
    relative_target: float = DEFAULT_RELATIVE_TARGET,
    max_halvings: int = DEFAULT_MAX_HALVINGS,
    start_substeps: int = 1,
) -> TruthIntegral:
    """Cumulative true Theta at ``report_indices`` (default: the last grid node), refining
    by step halving until the relative change at the last reported node is below
    ``relative_target`` or ``max_halvings`` is reached (the reported error then exceeds the
    target and the caller's assertion fails, as it should)."""
    if not len(times_s) == len(temperature_k) == len(rh_out_percent) or len(times_s) < 2:
        raise ValueError("need matching truth arrays with at least two nodes")
    indices = list(report_indices) if report_indices is not None else [len(times_s) - 1]
    m = start_substeps
    previous, rh_end = _integrate_once(
        model,
        times_s,
        temperature_k,
        rh_out_percent,
        package,
        ln_multiplier,
        indices,
        m,
        rh_in_start,
        FLOAT_BACKEND,
    )
    error = math.inf
    for _ in range(max_halvings):
        m *= 2
        current, rh_end = _integrate_once(
            model,
            times_s,
            temperature_k,
            rh_out_percent,
            package,
            ln_multiplier,
            indices,
            m,
            rh_in_start,
            FLOAT_BACKEND,
        )
        last = current[-1]
        error = abs(last - previous[-1]) / last if last > 0 else abs(last - previous[-1])
        previous = current
        if error < relative_target:
            break
    return TruthIntegral(tuple(float(v) for v in previous), float(rh_end), float(error), m)


def integrate_truth_mp(
    model: TruthModel,
    times_s: Sequence[float],
    temperature_k: Sequence[float],
    rh_out_percent: Sequence[float],
    package: PackageModel,
    *,
    substeps: int,
    ln_multiplier: float = 0.0,
    rh_in_start: float | None = None,
) -> Any:
    """The same trapezoid at ``substeps`` per interval in mpmath at 50 digits (spot check)."""
    values, _ = _integrate_once(
        model,
        times_s,
        temperature_k,
        rh_out_percent,
        package,
        ln_multiplier,
        [len(times_s) - 1],
        substeps,
        rh_in_start,
        MP_BACKEND,
    )
    return values[-1]

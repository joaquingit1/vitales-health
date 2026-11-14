"""Oracle-only targets of the vector catalogue (spec 15.1): quantities no engine function
computes, evaluated here at 50 significant digits from the formulas in the vectors."""

from collections.abc import Callable

from tests.oracle.precision import MP, Mpf

PERCENT = 100
GCI_SAFETY_FACTOR = MP.mpf("1.25")


def delta_h_over_r(delta_h: float, gas_constant: float) -> Mpf:
    """TV-MKT-1: dH / R (any consistent units)."""
    return MP.mpf(delta_h) / MP.mpf(gas_constant)


def water_loss_ratio(rh_alternative_percent: float, rh_reference_percent: float) -> Mpf:
    """TV-H-1: (100 - RH_reference) / (100 - RH_alternative)."""
    return (PERCENT - MP.mpf(rh_reference_percent)) / (PERCENT - MP.mpf(rh_alternative_percent))


def coverage_simulation_count(expected_coverage: float, target_mcse: float) -> Mpf:
    """TV-MC-1: n_sim = E(Cov)(1 - E(Cov)) / MCSE^2."""
    e = MP.mpf(expected_coverage)
    return e * (1 - e) / MP.mpf(target_mcse) ** 2


def first_order_half_life(rate_per_unit_time: float) -> Mpf:
    """TV-SF-1: t_1/2 = ln 2 / k (k the magnitude of the published negative slope)."""
    return MP.log(2) / abs(MP.mpf(rate_per_unit_time))


def gci(phi1: float, phi2: float, phi3: float, r21: float, r32: float) -> dict[str, Mpf]:
    """TV-GCI-1 to -3: Celik et al. grid convergence index by fixed-point iteration on p
    (procedure text of the vector). Percent outputs are in percent points."""
    f1, f2, f3 = MP.mpf(phi1), MP.mpf(phi2), MP.mpf(phi3)
    ra, rb = MP.mpf(r21), MP.mpf(r32)
    eps21, eps32 = f2 - f1, f3 - f2
    ratio = eps32 / eps21
    s = MP.sign(ratio)

    def q_of(p: Mpf) -> Mpf:
        return MP.log((ra**p - s) / (rb**p - s))

    def step(p: Mpf) -> Mpf:
        return abs(MP.log(abs(ratio)) + q_of(p)) / MP.log(ra)

    p = _fixed_point(step, abs(MP.log(abs(ratio))) / MP.log(ra))  # start from q = 0
    rp = ra**p
    phi_ext = (rp * f1 - f2) / (rp - 1)
    e_a = abs((f1 - f2) / f1)
    e_ext = abs((phi_ext - f1) / phi_ext)
    return {
        "p": p,
        "phi_ext21": phi_ext,
        "e_a21_percent": e_a * PERCENT,
        "e_ext21_percent": e_ext * PERCENT,
        "gci_fine21_percent": GCI_SAFETY_FACTOR * e_a / (rp - 1) * PERCENT,
    }


def _fixed_point(step: Callable[[Mpf], Mpf], start: Mpf, max_iterations: int = 10_000) -> Mpf:
    p = start
    for _ in range(max_iterations):
        nxt = step(p)
        if abs(nxt - p) <= MP.mpf(10) ** (-(MP.dps - 5)) * max(1, abs(nxt)):
            return nxt
        p = nxt
    raise ArithmeticError("GCI fixed point did not converge")

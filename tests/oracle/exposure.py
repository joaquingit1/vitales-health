"""Oracle for the mean kinetic temperature (spec 3.8) at 50 significant digits."""

from collections.abc import Sequence

from tests.oracle.precision import MP, R_MP, Mpf


def mean_kinetic_temperature(
    temperatures_k: Sequence[float],
    durations_s: Sequence[float],
    activation_energy_j_per_mol: float,
    gas_constant: Mpf = R_MP,
) -> Mpf:
    """T_mk = (dH/R) / (-ln(sum_i tau_i exp(-dH/(R T_i)) / sum_i tau_i)) (spec 3.8)."""
    if not temperatures_k or len(temperatures_k) != len(durations_s):
        raise ValueError("need matching, non-empty temperature and duration lists")
    if any(t <= 0 for t in temperatures_k) or any(tau < 0 for tau in durations_s):
        raise ValueError("temperatures must be positive and durations non-negative")
    h_over_r = MP.mpf(activation_energy_j_per_mol) / MP.mpf(gas_constant)
    total = MP.fsum(MP.mpf(tau) for tau in durations_s)
    if total <= 0:
        raise ValueError("total duration must be positive")
    weighted = MP.fsum(
        MP.mpf(tau) * MP.exp(-h_over_r / MP.mpf(t))
        for t, tau in zip(temperatures_k, durations_s, strict=True)
    )
    return h_over_r / (-MP.log(weighted / total))


def arithmetic_mean_temperature(temperatures_k: Sequence[float], durations_s: Sequence[float]) -> Mpf:
    """Time-weighted arithmetic mean (TV-MKT-5)."""
    total = MP.fsum(MP.mpf(tau) for tau in durations_s)
    return (
        MP.fsum(MP.mpf(t) * MP.mpf(tau) for t, tau in zip(temperatures_k, durations_s, strict=True)) / total
    )

"""Oracle for the radiation constants (spec 6), the parameter algebra (spec 9.3 to 9.5) and
the linear free-energy relationships (spec 7.3) at 50 significant digits."""

import dataclasses
from collections.abc import Sequence

from pharmacy.types import PackageDoc
from tests.oracle.kinetics import humidity_integral
from tests.oracle.microclimate import exchange_time_constant
from tests.oracle.precision import MP, R_MP, Mpf

GRAMS_PER_KILOGRAM = 1000
J_PER_KJ = 1000

PARAMETER_LN_K = "ln_k_ref_per_s"
PARAMETER_EA = "ea_j_per_mol"
PARAMETER_B = "b_per_percent_rh"


# --- Radiation (spec 6) ---------------------------------------------------------------------------
def k_dose_direct(g_value_mol_per_j: float, molar_mass_g_per_mol: float) -> Mpf:
    """k_D = G M / 1000 per Gy (spec 6.2)."""
    return MP.mpf(g_value_mol_per_j) * MP.mpf(molar_mass_g_per_mol) / GRAMS_PER_KILOGRAM


def k_dose_indirect(
    g_max_mol_per_j: float, concentration_mol_per_kg: float, half_scavenging_mol_per_kg: float
) -> Mpf:
    """k_D = G_eff / c = G_max / (c + c_half) per Gy (spec 6.3)."""
    return MP.mpf(g_max_mol_per_j) / (MP.mpf(concentration_mol_per_kg) + MP.mpf(half_scavenging_mol_per_kg))


def k_dose_indirect_from_g_eff(g_max: float, c: float, c_half: float) -> Mpf:
    """The same constant through G_eff(c) / c, the defining form (c > 0)."""
    g_eff = MP.mpf(g_max) * MP.mpf(c) / (MP.mpf(c) + MP.mpf(c_half))
    return g_eff / MP.mpf(c)


def upper_bound_loss_fraction(k_upper_per_gy: float, dose_rate_safety_factor: float, dose_gy: float) -> Mpf:
    """loss_upper = 1 - exp(-k_upper F D_cum) (spec 6.4)."""
    return -MP.expm1(-MP.mpf(k_upper_per_gy) * MP.mpf(dose_rate_safety_factor) * MP.mpf(dose_gy))


def central_loss_fraction(k_dose_per_gy: float, dose_gy: float) -> Mpf:
    """1 - exp(-k_D D) (spec 6.3)."""
    return -MP.expm1(-MP.mpf(k_dose_per_gy) * MP.mpf(dose_gy))


# --- Reference conversion and combination (spec 9.3 to 9.5) ---------------------------------------
def conversion_matrix(
    names: Sequence[str],
    from_temperature_k: float,
    from_rh_percent: float,
    to_temperature_k: float,
    to_rh_percent: float,
    gas_constant: Mpf = R_MP,
) -> list[list[Mpf]]:
    """A of spec 9.3: identity except the ln k row, which gets -(1/T' - 1/T)/R in the Ea
    column and (RH' - RH) in the B column."""
    if PARAMETER_LN_K not in names:
        raise ValueError("ln_k_ref_per_s must be present")
    size = len(names)
    a = [[MP.mpf(1) if i == j else MP.mpf(0) for j in range(size)] for i in range(size)]
    row = list(names).index(PARAMETER_LN_K)
    x = (1 / MP.mpf(to_temperature_k) - 1 / MP.mpf(from_temperature_k)) / MP.mpf(gas_constant)
    if PARAMETER_EA in names:
        a[row][list(names).index(PARAMETER_EA)] = -x
    if PARAMETER_B in names:
        a[row][list(names).index(PARAMETER_B)] = MP.mpf(to_rh_percent) - MP.mpf(from_rh_percent)
    return a


def convert_reference(
    names: Sequence[str],
    mean: Sequence[float],
    covariance: Sequence[Sequence[float]],
    from_temperature_k: float,
    from_rh_percent: float,
    to_temperature_k: float,
    to_rh_percent: float,
) -> tuple[list[Mpf], list[list[Mpf]]]:
    """ln k* = ln k0 - Ea (1/T* - 1/T0)/R + B (RH* - RH0); mean A m, covariance A C A^T."""
    a = conversion_matrix(names, from_temperature_k, from_rh_percent, to_temperature_k, to_rh_percent)
    size = len(names)
    m = [MP.mpf(v) for v in mean]
    c = [[MP.mpf(v) for v in row] for row in covariance]
    new_mean = [MP.fsum(a[i][k] * m[k] for k in range(size)) for i in range(size)]
    ac = [[MP.fsum(a[i][k] * c[k][j] for k in range(size)) for j in range(size)] for i in range(size)]
    new_cov = [[MP.fsum(ac[i][k] * a[j][k] for k in range(size)) for j in range(size)] for i in range(size)]
    return new_mean, new_cov


def precision_weighted(
    mu_prior: float, sd_prior: float, mu_computed: float, sd_computed: float
) -> tuple[Mpf, Mpf]:
    """1/s^2 = 1/s_p^2 + 1/s_c^2, mu = s^2 (mu_p/s_p^2 + mu_c/s_c^2) (spec 9.4); returns (mu, s)."""
    vp, vc = MP.mpf(sd_prior) ** 2, MP.mpf(sd_computed) ** 2
    var = 1 / (1 / vp + 1 / vc)
    return var * (MP.mpf(mu_prior) / vp + MP.mpf(mu_computed) / vc), MP.sqrt(var)


def class_prior_ln_k(mu_total: float, sd_total: float, n_pathways: int) -> tuple[Mpf, Mpf]:
    """mu_p = mu_total - ln N, s_p = s_total (spec 9.5)."""
    if n_pathways < 1:
        raise ValueError("N must be at least 1")
    return MP.mpf(mu_total) - MP.log(n_pathways), MP.mpf(sd_total)


@dataclasses.dataclass(frozen=True, slots=True)
class SplitMember:
    """One member of the 9.5 split: ``anchored`` when it has a computed-layer estimate
    (7.3.1); ``shift`` its Evans-Polanyi delta (None: no shift, delta 0) and ``sd_method``
    the shift's method error s_m in ln units (0 without a shift)."""

    anchored: bool
    shift: float | None = None
    sd_method: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class SplitPrior:
    """The 9.5 class prior of one member: block mean, standard deviation and shared weight,
    and for a free member of a group of two or more its free share (delta_i, s_m,i). For an
    anchored member this is the prior before the 9.4 combination (mu_total - ln N, s_p, 1)."""

    mu: Mpf
    sd: Mpf
    shared_weight: Mpf
    free_share: tuple[Mpf, Mpf] | None = None


def split_class_prior(mu_total: float, sd_total: float, members: Sequence[SplitMember]) -> list[SplitPrior]:
    """Spec 9.5 (shifts redistribute only the free group F, N_F of the N members):

    anchored i: mu_p,i = mu_total - ln N, s_p, w = 1
    free i:     mu_p,i = mu_total + ln(N_F / N) + delta_i - ln(sum_{j in F} exp(delta_j)), s_p, w = 1

    with s_p = s_total. A free member of a group of two or more carries the free share
    (delta_i, s_m,i), s_m,i = 0 when unshifted; a group of one carries none. Shifts on
    anchored members are ignored. The sum of exp(mu_p,i) over the split is exp(mu_total)."""
    if not members:
        raise ValueError("N must be at least 1")
    n = len(members)
    s_p = MP.mpf(sd_total)
    one = MP.mpf(1)
    equal = MP.mpf(mu_total) - MP.log(n)
    free = [i for i, m in enumerate(members) if not m.anchored]
    out: list[SplitPrior] = [SplitPrior(equal, s_p, one) for _ in members]
    if not free:
        return out
    deltas = {i: MP.mpf(0) if members[i].shift is None else MP.mpf(members[i].shift) for i in free}
    s_m = {i: MP.mpf(0) if members[i].shift is None else MP.mpf(members[i].sd_method) for i in free}
    log_sum = log_sum_exp([deltas[i] for i in free])
    for i in free:
        mu = MP.mpf(mu_total) + MP.log(MP.mpf(len(free)) / n) + deltas[i] - log_sum
        share = (deltas[i], s_m[i]) if len(free) >= 2 else None
        out[i] = SplitPrior(mu, s_p, one, share)
    return out


def log_sum_exp(values: Sequence[Mpf]) -> Mpf:
    top = max(values)
    return top + MP.log(MP.fsum(MP.exp(v - top) for v in values))


def free_group_ln_k(
    mu_p: Sequence[float],
    s_p: float,
    z_cls: float,
    deltas: Sequence[float],
    s_m: Sequence[float],
    z_f: Sequence[float],
) -> list[Mpf]:
    """Spec 9.5 draw of a free group of two or more:
    ln k_i = mu_p,i + s_p z_cls + log_softmax_F(delta + s_m z_F)_i - log_softmax_F(delta)_i,
    log_softmax_F(x)_i = x_i - ln sum_{j in F} exp(x_j)."""
    if not len(mu_p) == len(deltas) == len(s_m) == len(z_f) or len(mu_p) < 2:
        raise ValueError("a free group needs two or more members with one delta, s_m and z each")
    d = [MP.mpf(x) for x in deltas]
    moved = [MP.mpf(x) + MP.mpf(s) * MP.mpf(z) for x, s, z in zip(deltas, s_m, z_f, strict=True)]
    lse_d, lse_m = log_sum_exp(d), log_sum_exp(moved)
    common = MP.mpf(s_p) * MP.mpf(z_cls)
    return [MP.mpf(m) + common + (moved[i] - lse_m) - (d[i] - lse_d) for i, m in enumerate(mu_p)]


# --- Linear free-energy relationships (spec 7.3) ------------------------------------------------------
def hammett_delta_ln_k(rho: float, sigma_site_sum: float, sigma_anchor_sum: float) -> Mpf:
    """ln(10) rho (sum sigma_ingredient - sum sigma_anchor) (spec 7.3.2)."""
    return MP.log(10) * MP.mpf(rho) * (MP.mpf(sigma_site_sum) - MP.mpf(sigma_anchor_sum))


def taft_delta_ln_k(rho_star: float, delta: float, d_sigma_star: float, d_es: float) -> Mpf:
    """ln(10) (rho* d sigma* + delta d Es) (spec 7.3.3)."""
    return MP.log(10) * (MP.mpf(rho_star) * MP.mpf(d_sigma_star) + MP.mpf(delta) * MP.mpf(d_es))


def evans_polanyi_delta_ln_k(
    alpha: float,
    bde_site_kj_per_mol: float,
    bde_reference_kj_per_mol: float,
    reference_temperature_k: float,
    max_abs_delta_bde_kj_per_mol: float | None = None,
) -> Mpf:
    """delta_s = -alpha_EP (BDE_site - BDE_ref) x 1000 / (R T*) (spec 7.3.4); 0 when
    |BDE_site - BDE_ref| exceeds ``max_abs_delta_bde_kj_per_mol`` (outside the domain)."""
    if outside_applicability_domain(
        bde_site_kj_per_mol, bde_reference_kj_per_mol, max_abs_delta_bde_kj_per_mol
    ):
        return MP.mpf(0)
    diff = MP.mpf(bde_site_kj_per_mol) - MP.mpf(bde_reference_kj_per_mol)
    return -MP.mpf(alpha) * diff * J_PER_KJ / (R_MP * MP.mpf(reference_temperature_k))


def outside_applicability_domain(
    bde_site_kj_per_mol: float, bde_reference_kj_per_mol: float, max_abs_delta_bde_kj_per_mol: float | None
) -> bool:
    """Spec 7.3.4: the relation is not applied when |BDE_site - BDE_ref| exceeds the
    entry's limit (strictly greater; equality stays inside)."""
    if max_abs_delta_bde_kj_per_mol is None:
        return False
    diff = MP.mpf(bde_site_kj_per_mol) - MP.mpf(bde_reference_kj_per_mol)
    return bool(abs(diff) > MP.mpf(max_abs_delta_bde_kj_per_mol))


def combine_sites(delta_ln_k_per_site: Sequence[float], n_anchor_sites: int) -> Mpf:
    """ln(sum_s exp(delta_s) / n_ref) (spec 7.3.5)."""
    if not delta_ln_k_per_site or n_anchor_sites < 1:
        raise ValueError("need at least one site and n_ref >= 1")
    return MP.log(MP.fsum(MP.exp(MP.mpf(d)) for d in delta_ln_k_per_site) / n_anchor_sites)


EVANS_POLANYI_REFERENCE_HYDROGENS = {"trimethylamine": 9, "phenol": 1, "catechol": 2}
"""Spec 7.3.5: sum_r n_H,r over the reference molecule's sites of the class."""


def combine_sites_weighted(
    delta_ln_k_per_site: Sequence[float], hydrogens_per_site: Sequence[int], reference_hydrogens: int
) -> Mpf:
    """Evans-Polanyi sites weighted by abstractable hydrogens (spec 7.3.5):
    ln( sum_s n_H,s exp(delta_s) / sum_r n_H,r )."""
    if (
        not delta_ln_k_per_site
        or len(delta_ln_k_per_site) != len(hydrogens_per_site)
        or reference_hydrogens < 1
    ):
        raise ValueError("need one hydrogen count per site and a positive reference count")
    if any(n < 1 for n in hydrogens_per_site):
        raise ValueError("every site needs at least one abstractable hydrogen")
    terms = [
        MP.mpf(n) * MP.exp(MP.mpf(d)) for d, n in zip(delta_ln_k_per_site, hydrogens_per_site, strict=True)
    ]
    return MP.log(MP.fsum(terms) / reference_hydrogens)


def reference_rate(
    ln_k: float,
    sd_ln_k: float,
    temperature_k: float,
    temperature_sd_k: float,
    rh_percent: float,
    ea: float,
    sd_ea: float,
    b: float,
    sd_b: float,
    reference_temperature_k: float,
    reference_rh_percent: float,
) -> tuple[Mpf, Mpf]:
    """Unpackaged calibration/anchor conversion and variance, spec 9.3.

    Total-rate rows undergo this conversion offline, never on a stored reference prior.
    rh_sd_percent describes the validation zone, not an extra 9.3 variance term.
    """
    t, target = MP.mpf(temperature_k), MP.mpf(reference_temperature_k)
    x = (1 / target - 1 / t) / R_MP
    d_rh = MP.mpf(reference_rh_percent) - MP.mpf(rh_percent)
    mean = MP.mpf(ln_k) - MP.mpf(ea) * x + MP.mpf(b) * d_rh
    variance = (
        MP.mpf(sd_ln_k) ** 2
        + (x * MP.mpf(sd_ea)) ** 2
        + (d_rh * MP.mpf(sd_b)) ** 2
        + (MP.mpf(ea) * MP.mpf(temperature_sd_k) / (R_MP * t**2)) ** 2
    )
    return mean, variance


def packaged_reference_rate(
    ln_k: float,
    sd_ln_k: float,
    temperature_k: float,
    temperature_sd_k: float,
    rh_percent: float,
    ea: float,
    sd_ea: float,
    b: float,
    sd_b: float,
    reference_temperature_k: float,
    reference_rh_percent: float,
    duration_s: float,
    package: PackageDoc | None,
    zone_ln_mean: float = 0.0,
    zone_ln_sd: float = 0.0,
) -> tuple[Mpf, Mpf]:
    """Match the measured average rate to the independent package integral, spec 9.3.

    The B sensitivity is the derivative of minus log(mean humidity multiplier).
    This reduces to RH* - RH0 for open dish and constant internal RH.
    """
    if duration_s <= 0:
        raise ValueError("duration must be positive")
    mean, variance = reference_rate(
        ln_k,
        sd_ln_k,
        temperature_k,
        temperature_sd_k,
        rh_percent,
        ea,
        sd_ea,
        b,
        0.0,
        reference_temperature_k,
        reference_rh_percent,
    )
    out, duration = MP.mpf(rh_percent), MP.mpf(duration_s)
    start, tau = out, MP.mpf(0)
    if package is not None:
        moisture = package["moisture_model"]
        tau = exchange_time_constant(package, temperature_k)
        if moisture["kind"] == "sealed_constant":
            sealed = moisture["sealed_rh_percent"]
            if sealed is None:
                raise ValueError("sealed_constant needs sealed_rh_percent")
            start = MP.mpf(sealed["value"])
        elif moisture["kind"] == "first_order_exchange":
            initial = moisture["initial_internal_rh_percent"]
            start = out if initial is None else MP.mpf(initial["value"])

    def log_humidity(beta: Mpf) -> Mpf:
        return beta * (out - reference_rh_percent) + MP.log(
            humidity_integral(beta * (start - out), duration, tau) / duration
        )

    humidity_log = log_humidity(MP.mpf(b))
    sensitivity = -MP.diff(log_humidity, MP.mpf(b))
    # reference_rate already applied the ambient humidity map; replace it with the integral.
    mean -= MP.mpf(b) * (MP.mpf(reference_rh_percent) - out) + humidity_log
    mean -= MP.mpf(zone_ln_mean)
    variance += (sensitivity * MP.mpf(sd_b)) ** 2 + MP.mpf(zone_ln_sd) ** 2
    return mean, variance


def correlated_anchor_draw(
    mu_post: float,
    sd_post: float,
    sd_prior: float,
    variance_computed: float,
    zone_sd: float,
    z_cls: float,
    z_lnk: float,
    zone_ln_deviation: float,
) -> Mpf:
    """Exact 9.3 correlated draw using the same zone deviation as the lot kinetics."""
    vp, vc, vz = MP.mpf(sd_post) ** 2, MP.mpf(variance_computed), MP.mpf(zone_sd) ** 2
    if sd_prior <= 0 or vc <= 0 or vc < vz:
        raise ValueError("invalid anchor variance decomposition")
    weight = vp / vc
    return (
        MP.mpf(mu_post)
        + vp / MP.mpf(sd_prior) * MP.mpf(z_cls)
        + weight * MP.sqrt(vc - vz) * MP.mpf(z_lnk)
        - weight * MP.mpf(zone_ln_deviation)
    )


def correlated_anchor_moments(
    sd_prior: float,
    anchor_variance_without_zone: float,
    zone_sd: float,
) -> tuple[Mpf, Mpf, Mpf]:
    """(ground-rate variance, covariance with zone, same-zone effective variance).

    Spec 9.3 retains the marginal zone prior, with its stated conservative excess.
    """
    vp, va, vz = MP.mpf(sd_prior) ** 2, MP.mpf(anchor_variance_without_zone), MP.mpf(zone_sd) ** 2
    if vp <= 0 or va < 0 or va + vz <= 0:
        raise ValueError("invalid anchor variance decomposition")
    vc = va + vz
    post = vp * vc / (vp + vc)
    covariance = -post * vz / vc
    return post, covariance, post + vz + 2 * covariance

"""Parameter resolution: measured model > (assay update, in update.py) > computed > class prior.

Owner: B2. Contract: docs/ENGINE-SPEC.md sections 9.1 to 9.6. Produces the
``StabilityModel`` that the numerical core consumes.

Every parameter is expressed at the engine reference conditions (T*, RH*) of
engine-settings.json. Class-based blocks are diagonal normal in the spec 9.4 order
(ln k*, Ea, B, ln k_vis, ln k_uv, ln k_dose, ln k_dose_frozen, ln m_frozen); lognormal priors
enter as the normal of their logarithm and fixed priors with variance 0. Ea is truncated
below at its prior's lower bound (``ParameterBlock.ea_lower_j_per_mol``); the block holds the
location and scale of that truncated normal (spec 9.4).
"""

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from scipy.special import exprel

from pharmacy import microclimate
from pharmacy.chemistry import lfer
from pharmacy.chemistry.pathways import PathwayMatch, identify_pathways
from pharmacy.chemistry.radiolysis import LnNormal, radiolysis_prior, weakest_basis
from pharmacy.config import ConfigBundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, SECONDS_PER_DAY
from pharmacy.errors import ConfigReferenceError, InputError, ModelDomainError
from pharmacy.kinetics import humidity_integral
from pharmacy.types import (
    ESTIMATE_BASIS_STRENGTH,
    ActiveIngredientDoc,
    Anchor,
    Basis,
    ComputedDetail,
    DegradantIdentification,
    DegradantSpec,
    DistributionFamily,
    DoseFormFamily,
    EstimateBasis,
    FamilyPriors,
    FreeShare,
    PackageDoc,
    ParameterBlock,
    ParameterEstimate,
    ParameterLayer,
    ParameterName,
    Pathway,
    Prior,
    ProductDoc,
    RateLawKind,
    ReactionClassDoc,
    ReactiveSite,
    Segment,
    SourceLayer,
    StabilityModel,
    StabilityModelDoc,
    TotalRatePrior,
    ValidatedRange,
    ZoneCorrection,
)

PARAMETER_ORDER: tuple[ParameterName, ...] = (
    "ln_k_ref_per_s",
    "ea_j_per_mol",
    "b_per_percent_rh",
    "ln_k_vis_per_lux_h",
    "ln_k_uv_per_w_h_per_m2",
    "ln_k_dose_per_gy",
    "ln_k_dose_frozen_per_gy",
    "ln_frozen_rate_multiplier",
)
"""Order of the names in a class-based ``ParameterBlock`` (spec 9.4)."""

PARAMETER_UNITS: Mapping[ParameterName, str] = {
    "ln_k_ref_per_s": "ln(1/s)",
    "ea_j_per_mol": "J/mol",
    "b_per_percent_rh": "1/%RH",
    "ln_k_vis_per_lux_h": "ln(1/(lux h))",
    "ln_k_uv_per_w_h_per_m2": "ln(1/(W h/m2))",
    "ln_k_dose_per_gy": "ln(1/Gy)",
    "ln_k_dose_frozen_per_gy": "ln(1/Gy)",
    "ln_frozen_rate_multiplier": "ln(1)",
}

RATE_PARAMETER: Mapping[str, ParameterName] = {
    "thermal": "ln_k_ref_per_s",
    "light": "ln_k_vis_per_lux_h",
    "radiation": "ln_k_dose_per_gy",
}
"""The rate-level parameter whose layer is the pathway's layer, per driver (spec 9.2)."""

NO_EA_LOWER_BOUND = 0.0
"""``ParameterBlock.ea_lower_j_per_mol`` of a block without Ea (light, radiolysis). The field
is never read for such blocks; it is not a physical value."""

KINDS_NEEDING_PARAMETERS: Mapping[RateLawKind, str] = {"Fn": "order_n", "An": "avrami_n", "B1": "alpha0"}

_LAYER_STRENGTH: Mapping[SourceLayer, int] = {"measured_model": 4, "computed": 2, "class_prior": 1}


# =========================================================================================
# Oracle-checked scalar functions (spec 15.2)
# =========================================================================================


def _check_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ModelDomainError(f"{name} must be finite, got {value!r}")


def _check_temperature(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ModelDomainError(f"{name} must be a positive temperature in K, got {value!r}")


def convert_reference(
    names: Sequence[ParameterName],
    mean: Sequence[float],
    covariance: Sequence[Sequence[float]],
    from_temperature_k: float,
    from_rh_percent: float,
    to_temperature_k: float,
    to_rh_percent: float,
) -> tuple[tuple[float, ...], tuple[tuple[float, ...], ...]]:
    """Exact linear map of (ln k, Ea, B, ...) between reference conditions (spec 9.3):
    ln k' = ln k - Ea (1/T' - 1/T)/R + B (RH' - RH); other parameters unchanged. The mean
    maps through A and the covariance as A C A^T. Requires ln_k_ref_per_s in ``names``;
    absent Ea or B count as 0 with zero variance.

    The covariance is computed on and above the diagonal and mirrored, so the result is
    exactly symmetric. Raises ``ModelDomainError`` for a missing ln k, repeated names,
    shapes that do not match, non-finite values or non-positive temperatures."""
    n = len(names)
    if len(set(names)) != n:
        raise ModelDomainError(f"parameter names repeat: {list(names)}")
    if "ln_k_ref_per_s" not in names:
        raise ModelDomainError("convert_reference needs ln_k_ref_per_s")
    if len(mean) != n or len(covariance) != n or any(len(row) != n for row in covariance):
        raise ModelDomainError(f"mean and covariance must match {n} names")
    for value in [*mean, *(c for row in covariance for c in row), from_rh_percent, to_rh_percent]:
        _check_finite("parameter", value)
    _check_temperature("from_temperature_k", from_temperature_k)
    _check_temperature("to_temperature_k", to_temperature_k)

    row_k = names.index("ln_k_ref_per_s")
    transform = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    if "ea_j_per_mol" in names:
        transform[row_k][names.index("ea_j_per_mol")] = -(
            (1.0 / to_temperature_k - 1.0 / from_temperature_k) / GAS_CONSTANT_J_PER_MOL_K
        )
    if "b_per_percent_rh" in names:
        transform[row_k][names.index("b_per_percent_rh")] = to_rh_percent - from_rh_percent

    try:
        new_mean = tuple(math.fsum(transform[i][j] * mean[j] for j in range(n)) for i in range(n))
        upper: dict[tuple[int, int], float] = {}
        for i in range(n):
            for j in range(i, n):
                upper[(i, j)] = math.fsum(
                    transform[i][a] * covariance[a][b] * transform[j][b] for a in range(n) for b in range(n)
                )
        new_cov = tuple(tuple(upper[(min(i, j), max(i, j))] for j in range(n)) for i in range(n))
    except (OverflowError, ValueError) as exc:
        raise ModelDomainError(f"reference conversion exceeds the numerical domain: {exc}") from exc
    for value in (*new_mean, *(c for row in new_cov for c in row)):
        _check_finite("converted parameter", value)
    return new_mean, new_cov


def precision_weighted(
    mu_prior: float, sd_prior: float, mu_computed: float, sd_computed: float
) -> tuple[float, float]:
    """Normal-normal combination (spec 9.4): 1/s^2 = 1/s_p^2 + 1/s_c^2,
    mu = s^2 (mu_p/s_p^2 + mu_c/s_c^2).

    Evaluated with the ratio of the smaller sd to the larger, so squaring large or tiny
    deviations cannot overflow or underflow the denominator. Both standard
    deviations must be positive (``ModelDomainError``)."""
    for name, value in (("mu_prior", mu_prior), ("mu_computed", mu_computed)):
        _check_finite(name, value)
    for name, value in (("sd_prior", sd_prior), ("sd_computed", sd_computed)):
        if not math.isfinite(value) or value <= 0.0:
            raise ModelDomainError(f"{name} must be positive and finite, got {value!r}")
    smaller, larger = min(sd_prior, sd_computed), max(sd_prior, sd_computed)
    relative_variance = (smaller / larger) ** 2
    denominator = 1.0 + relative_variance
    smaller_weight = 1.0 / denominator
    larger_weight = relative_variance / denominator
    prior_weight, computed_weight = (
        (smaller_weight, larger_weight) if sd_prior <= sd_computed else (larger_weight, smaller_weight)
    )
    mean = mu_prior * prior_weight + mu_computed * computed_weight
    sd = smaller / math.sqrt(denominator)
    _check_finite("combined mean", mean)
    return mean, sd


def class_prior_ln_k(total: TotalRatePrior, n_pathways: int) -> tuple[float, float]:
    """Per-pathway class prior for ln k at the engine reference (spec 9.5): the product
    total-rate prior split equally among the N fallback destructive thermal pathways:
    mu - ln N, same sd (the shared draw keeps the total's spread). The stored prior has
    already been converted offline, including its package physics and reference-conversion
    variance. Its calibration storage fields must not trigger another conversion."""
    if isinstance(n_pathways, bool) or not isinstance(n_pathways, int) or n_pathways < 1:
        raise ModelDomainError(f"the split needs at least one pathway, got {n_pathways!r}")
    prior = total["ln_k_ref_per_s"]
    if prior["family"] != "normal":
        raise ModelDomainError(f"total-rate prior must be normal, got {prior['family']!r}")
    _check_finite("total-rate mu", prior["mu"])
    _check_finite("total-rate sigma", prior["sigma"])
    if prior["sigma"] <= 0.0:
        raise ModelDomainError("total-rate sigma must be positive")
    return prior["mu"] - math.log(n_pathways), prior["sigma"]


@dataclasses.dataclass(frozen=True, slots=True)
class FreePrior:
    """A free member of the total-rate split (spec 9.5): prior mean mu_p,i and share pi_i of
    the free group's part of the total."""

    mu_ln_k: float
    share: float


def split_class_priors(
    total: TotalRatePrior, n_anchored: int, deltas: Sequence[float]
) -> tuple[float, float, tuple[FreePrior, ...]]:
    """The class priors of the total-rate split (spec 9.5) with ``n_anchored`` anchored
    members and the free group F given by its Evans-Polanyi shifts ``deltas`` (0 for a free
    member without one).

    Returns (mu_total - ln N, s_p, free priors) with N = n_anchored + N_F: anchored members
    take mu_total - ln N; free member i takes
    mu_p,i = mu_total + ln(N_F/N) + delta_i - ln(sum_F exp(delta_j)) and the share
    pi_i = exp(delta_i) / sum_F exp(delta_j) (log-sum-exp). Every member's block sd is s_p.
    The exp(mu) of all members sum to exp(mu_total)."""
    if isinstance(n_anchored, bool) or not isinstance(n_anchored, int) or n_anchored < 0:
        raise ModelDomainError(f"the number of anchored members must be an integer >= 0, got {n_anchored!r}")
    for value in deltas:
        _check_finite("split delta", value)
    n_free = len(deltas)
    anchored_mu, s_p = class_prior_ln_k(total, n_anchored + n_free)
    if not n_free:
        return anchored_mu, s_p, ()
    mu_total = total["ln_k_ref_per_s"]["mu"]
    largest = max(deltas)
    weights = [math.exp(delta - largest) for delta in deltas]
    denominator = math.fsum(weights)
    log_sum = largest + math.log(denominator)
    offset = math.log(n_free / (n_anchored + n_free))
    return (
        anchored_mu,
        s_p,
        tuple(
            FreePrior(mu_ln_k=mu_total + offset + delta - log_sum, share=weight / denominator)
            for delta, weight in zip(deltas, weights, strict=True)
        ),
    )


def _anchor_zone_prior(anchor: Anchor, bundle: ConfigBundle) -> Prior:
    zone_id = anchor["storage_zone_id"]
    for zone in bundle.zones["zones"]:
        if zone["zone_id"] == zone_id:
            return zone["rate_multiplier"]
    raise ConfigReferenceError("storage-zones", [f"unknown anchor zone {zone_id!r}"])


def _zone_ln_moments(prior: Prior) -> tuple[float, float]:
    for name in ("mu", "sigma"):
        _check_finite(f"zone multiplier {name}", prior[name])
    if prior["family"] == "fixed" and prior["mu"] > 0.0 and prior["sigma"] == 0.0:
        return math.log(prior["mu"]), 0.0
    if prior["family"] == "lognormal" and prior["sigma"] >= 0.0:
        return prior["mu"], prior["sigma"]
    raise ModelDomainError("anchor zone multiplier must be positive fixed or lognormal")


def _anchor_humidity(anchor: Anchor, b_mean: float, bundle: ConfigBundle) -> tuple[float, float]:
    """Return log(mean exp(B RH_in)) and rate-weighted RH (spec 9.3).

    The second value is the derivative of the first with respect to B. At B = 0 use
    the continuous limit, the time mean. See round2-CX-C R1 for the variance ambiguity.
    """
    outside = anchor["rh_percent"]["value"]
    package_id = anchor["package_id"]
    if package_id is None:
        return b_mean * outside, outside
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    if package_id not in packages:
        raise ConfigReferenceError("packages", [f"unknown anchor package {package_id!r}"])
    days = anchor["duration_days"]
    if days is None or not math.isfinite(days["value"]) or days["value"] <= 0.0:
        raise ModelDomainError("a packaged anchor needs a positive finite duration_days")
    duration = days["value"] * SECONDS_PER_DAY
    _check_finite("anchor duration", duration)
    segment = Segment(
        t0_s=0.0,
        t1_s=duration,
        zone_id=anchor["storage_zone_id"],
        package_id=package_id,
        temperature_k=anchor["temperature_k"]["value"],
        relative_humidity_percent=outside,
        relative_humidity_low_percent=outside,
        dose_rate_gy_per_s=0.0,
        illuminance_lux=0.0,
        uv_w_per_m2=0.0,
        gap_quantities=(),
        declared_quantities=(),
    )
    trace = microclimate.internal_rh_trace((segment,), packages)
    start = trace.rh_in_start_high[0]
    tau = trace.time_constant_s[0]
    if math.isinf(tau):
        return b_mean * start, start
    if tau == 0.0:
        return b_mean * outside, outside
    numerics = bundle.settings["numerics"]
    tolerance = numerics["series_relative_tolerance"]["value"]
    span = duration / tau
    relaxed_fraction = -math.expm1(-span)
    relaxation_mean = 1.0 if span == 0.0 else relaxed_fraction / span
    if b_mean == 0.0:
        # Exact time mean of the engine's exponential trace, the B -> 0 limit.
        return 0.0, outside + (start - outside) * relaxation_mean
    x = b_mean * (start - outside)
    integral = humidity_integral(
        x,
        duration,
        tau,
        rel_tol=tolerance,
        max_terms=int(numerics["series_max_terms"]["value"]),
    )
    if integral <= 0.0:
        raise ModelDomainError("anchor humidity integral must be positive")
    mean_integrand = integral / duration
    log_mean = math.log(mean_integrand)
    # J_x = tau (exp(x) - exp(x exp(-span))) / x. Factoring at the larger
    # exponent and using exprel avoids cancellation at x = 0 and short durations.
    largest_exponent = x if x >= 0.0 else x * math.exp(-span)
    weighted_relaxation = (
        math.exp(largest_exponent - log_mean) * float(exprel(-abs(x) * relaxed_fraction)) * relaxation_mean
    )
    weighted_rh = outside + (start - outside) * weighted_relaxation
    return b_mean * outside + log_mean, weighted_rh


def anchor_at_reference(
    anchor: Anchor,
    ea_mean_j_per_mol: float,
    ea_sd_j_per_mol: float,
    b_mean: float,
    b_sd: float,
    reference_temperature_k: float,
    reference_rh_percent: float,
    *,
    bundle: ConfigBundle,
) -> tuple[float, float]:
    """Ground-equivalent anchor ln k and sd at the engine reference (spec 9.3).

    Match Theta/duration using engine microclimate and humidity integration when the
    anchor names a package. Subtract the configured zone's ln multiplier mean and add
    its ln variance, along with the specified Ea, B and temperature terms. RH0 in the
    B variance term is the rate-weighted internal RH, the derivative of the integrated
    log rate with respect to B (round2-CX-C R1).

    ``bundle`` resolves packages, zone multipliers and numerical settings at conversion
    time. Raises ``ConfigReferenceError`` for unknown package/zone references and
    ``ModelDomainError`` for unusable parameters, durations or multipliers. The engine
    humidity integral can raise ``ComputationError`` or ``ConvergenceError``.
    """
    prior = anchor["ln_k_per_s"]
    if prior["family"] != "normal":
        raise ModelDomainError(f"anchor {anchor['anchor_id']!r} ln k must be normal, got {prior['family']!r}")
    t0 = anchor["temperature_k"]["value"]
    s_t = anchor["temperature_sd_k"]["value"]
    rh0 = anchor["rh_percent"]["value"]
    _check_temperature("anchor temperature", t0)
    _check_temperature("reference temperature", reference_temperature_k)
    for name, value in (
        ("anchor mu", prior["mu"]),
        ("anchor sigma", prior["sigma"]),
        ("anchor temperature sd", s_t),
        ("anchor RH", rh0),
        ("Ea mean", ea_mean_j_per_mol),
        ("Ea sd", ea_sd_j_per_mol),
        ("B mean", b_mean),
        ("B sd", b_sd),
        ("reference RH", reference_rh_percent),
    ):
        _check_finite(name, value)
    if min(prior["sigma"], s_t, ea_sd_j_per_mol, b_sd) < 0.0:
        raise ModelDomainError("anchor and parameter standard deviations must be nonnegative")
    zone_mean, zone_sd = _zone_ln_moments(_anchor_zone_prior(anchor, bundle))
    try:
        log_humidity, weighted_rh = _anchor_humidity(anchor, b_mean, bundle)
        x = (1.0 / reference_temperature_k - 1.0 / t0) / GAS_CONSTANT_J_PER_MOL_K
        d_rh = reference_rh_percent - weighted_rh
        mean = prior["mu"] - ea_mean_j_per_mol * x + b_mean * reference_rh_percent - log_humidity - zone_mean
        sd = math.hypot(
            prior["sigma"],
            x * ea_sd_j_per_mol,
            d_rh * b_sd,
            ea_mean_j_per_mol * s_t / (GAS_CONSTANT_J_PER_MOL_K * t0 * t0),
            zone_sd,
        )
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        raise ModelDomainError(f"anchor reference conversion is outside the numerical domain: {exc}") from exc
    _check_finite("converted anchor mean", mean)
    _check_finite("converted anchor sd", sd)
    return mean, sd


# =========================================================================================
# Layers and estimate basis (spec 9.2, 9.6)
# =========================================================================================


def pathway_layer(pathway: Pathway) -> SourceLayer:
    """The layer of a pathway's rate-level parameter (spec 9.2)."""
    name = RATE_PARAMETER[pathway.driver]
    for estimate in pathway.parameters.estimates:
        if estimate.name == name:
            return estimate.source_layer
    raise ModelDomainError(f"pathway {pathway.pathway_id!r} has no {name} estimate")


def parameter_layers(pathway: Pathway) -> tuple[ParameterLayer, ...]:
    """Layer and method of every parameter of a pathway, in block order (spec 9.2)."""
    return tuple(
        ParameterLayer(name=e.name, layer=e.source_layer, method=e.method)
        for e in pathway.parameters.estimates
    )


def weakest_layer(layers: Iterable[EstimateBasis]) -> EstimateBasis:
    """The weakest of the given layers by spec 9.6 strength (measured_model 4 >
    assay_updated 3 > computed 2 > class_prior 1). Raises ``ModelDomainError`` when empty."""
    items = list(layers)
    if not items:
        raise ModelDomainError("weakest_layer needs at least one layer")
    return min(items, key=lambda layer: ESTIMATE_BASIS_STRENGTH[layer])


def nominal_shares(
    model: StabilityModel,
    nominal_theta: Mapping[str, float],
    alpha: float,
    rho: float,
    include_radiation: bool,
) -> dict[str, float]:
    """Nominal share of every pathway (spec 9.6): destructive pathways share the nominal
    Theta (radiolysis only when it enters potency, spec 5.6); for a racemizing ingredient
    the racemization share is s_r = (1 - rho/f0)/(1 - (1 - alpha) rho/f0), split among
    racemization pathways by their Theta, and the destructive shares are scaled by
    (1 - s_r). Shares are 0 when nothing accrued."""
    f0 = model.initial_active_fraction
    for name, value in (("alpha", alpha), ("rho", rho), ("f0", f0)):
        _check_finite(name, value)
    destructive = [
        p
        for p in model.pathways
        if p.role == "destructive" and (p.driver != "radiation" or include_radiation)
    ]
    racemization = [p for p in model.pathways if p.role == "racemization"]
    for pathway in model.pathways:
        theta = nominal_theta.get(pathway.pathway_id)
        if theta is None or not math.isfinite(theta) or theta < 0.0:
            raise ModelDomainError(f"nominal Theta of {pathway.pathway_id!r} missing or invalid: {theta!r}")
    shares = {p.pathway_id: 0.0 for p in model.pathways}
    s_r = 0.0
    if racemization:
        if f0 <= 0.0:
            raise ModelDomainError(f"initial active fraction must be positive, got {f0!r}")
        numerator = 1.0 - rho / f0
        denominator = 1.0 - (1.0 - alpha) * rho / f0
        s_r = 0.0 if numerator == 0.0 or denominator == 0.0 else numerator / denominator
        theta_r = math.fsum(nominal_theta[p.pathway_id] for p in racemization)
        for pathway in racemization:
            shares[pathway.pathway_id] = (
                0.0 if theta_r == 0.0 else s_r * nominal_theta[pathway.pathway_id] / theta_r
            )
    theta_d = math.fsum(nominal_theta[p.pathway_id] for p in destructive)
    for pathway in destructive:
        shares[pathway.pathway_id] = (
            0.0 if theta_d == 0.0 else (1.0 - s_r) * nominal_theta[pathway.pathway_id] / theta_d
        )
    return shares


@dataclasses.dataclass(frozen=True, slots=True)
class BasisSummary:
    """Spec 9.6 outcome: ``prior_basis`` (weakest contributing layer), ``estimate_basis``
    (with ``assay_updated`` when an assay was used), the contributing pathway ids and the
    ``computed_only`` flag."""

    prior_basis: SourceLayer
    estimate_basis: EstimateBasis
    contributing: tuple[str, ...]
    computed_only: bool


def basis_summary(
    model: StabilityModel, shares: Mapping[str, float], share_threshold: float, assay_used: bool
) -> BasisSummary:
    """Estimate basis of spec 9.6: a pathway contributes when its nominal share is at least
    ``share_threshold``; if none does, every pathway counts."""
    _check_finite("share_threshold", share_threshold)
    contributing = [p for p in model.pathways if shares.get(p.pathway_id, 0.0) >= share_threshold]
    if not contributing:
        contributing = list(model.pathways)
    layers = [pathway_layer(p) for p in contributing]
    prior = min(layers, key=lambda layer: _LAYER_STRENGTH[layer])
    estimate = weakest_layer([prior, "assay_updated"]) if assay_used else prior
    measured = any(layer == "measured_model" for layer in layers)
    return BasisSummary(
        prior_basis=prior,
        estimate_basis=estimate,
        contributing=tuple(p.pathway_id for p in contributing),
        computed_only=not measured and not assay_used,
    )


def computed_cache_keys(model: StabilityModel) -> tuple[str, ...]:
    """Every xTB cache key the model used, sorted and unique (``Pins.computed_cache_keys``)."""
    keys: set[str] = set()
    for pathway in model.pathways:
        if pathway.computed is not None:
            keys.update(pathway.computed.cache_keys)
    return tuple(sorted(keys))


# =========================================================================================
# Building blocks
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Component:
    """One parameter of a diagonal block with its provenance."""

    name: ParameterName
    mean: float
    variance: float
    estimate: ParameterEstimate


def _estimate(
    name: ParameterName,
    family: DistributionFamily,
    mu: float,
    sigma: float,
    layer: SourceLayer,
    method: str,
    source_ids: Iterable[str],
    basis: Basis,
    note: str,
    lower: float | None = None,
    upper: float | None = None,
) -> ParameterEstimate:
    return ParameterEstimate(
        name=name,
        family=family,
        mu=mu,
        sigma=sigma,
        lower=lower,
        upper=upper,
        unit=PARAMETER_UNITS[name],
        source_layer=layer,
        method=method,
        source_ids=tuple(sorted(set(source_ids))),
        basis=basis,
        note=note,
    )


def _log_component(name: ParameterName, prior: Prior, label: str, method: str) -> _Component:
    """A lognormal prior as the normal of its logarithm; a fixed prior as ln(value) with
    variance 0 (spec 9.4)."""
    if prior["family"] == "lognormal":
        mu, sigma = prior["mu"], prior["sigma"]
        family: DistributionFamily = "normal"
    elif prior["family"] == "fixed":
        if prior["mu"] <= 0.0:
            raise ModelDomainError(
                f"{label}: a fixed multiplier or rate must be positive, got {prior['mu']!r}"
            )
        mu, sigma = math.log(prior["mu"]), 0.0
        family = "fixed"
    else:
        raise ModelDomainError(f"{label}: expected a lognormal or fixed prior, got {prior['family']!r}")
    estimate = _estimate(
        name,
        family,
        mu,
        sigma,
        "class_prior",
        method,
        prior["source_ids"],
        prior["basis"],
        f"{label}: {prior['family']} prior entered as the normal of its logarithm. {prior['note']}".strip(),
    )
    return _Component(name, mu, sigma * sigma, estimate)


def _linear_component(name: ParameterName, prior: Prior, label: str) -> _Component:
    """A normal or fixed prior on the parameter itself (B)."""
    if prior["family"] not in ("normal", "fixed"):
        raise ModelDomainError(f"{label}: expected a normal or fixed prior, got {prior['family']!r}")
    estimate = _estimate(
        name,
        prior["family"],
        prior["mu"],
        prior["sigma"],
        "class_prior",
        "class_prior",
        prior["source_ids"],
        prior["basis"],
        f"{label}. {prior['note']}".strip(),
    )
    return _Component(name, prior["mu"], prior["sigma"] ** 2, estimate)


def _ea_component(prior: Prior | None, label: str) -> tuple[_Component, float]:
    """Ea as the location and scale of its truncated normal; returns the lower bound."""
    if prior is None:
        raise ConfigReferenceError("reaction-classes", [f"{label}: no Ea prior for a thermal pathway"])
    if prior["family"] != "truncated_normal" or prior["lower"] is None:
        raise ModelDomainError(f"{label}: Ea prior must be a truncated normal with a lower bound")
    estimate = _estimate(
        "ea_j_per_mol",
        "truncated_normal",
        prior["mu"],
        prior["sigma"],
        "class_prior",
        "class_prior",
        prior["source_ids"],
        prior["basis"],
        f"{label}: location and scale of the truncated normal. {prior['note']}".strip(),
        lower=prior["lower"],
        upper=prior["upper"],
    )
    return _Component("ea_j_per_mol", prior["mu"], prior["sigma"] ** 2, estimate), prior["lower"]


def _block(components: Sequence[_Component], ea_lower: float, shared_weight: float) -> ParameterBlock:
    ordered = sorted(components, key=lambda c: PARAMETER_ORDER.index(c.name))
    n = len(ordered)
    for component in ordered:
        _check_finite(f"{component.name} mean", component.mean)
        if not math.isfinite(component.variance) or component.variance < 0.0:
            raise ModelDomainError(
                f"{component.name} variance must be non-negative, got {component.variance!r}"
            )
    if not 0.0 <= shared_weight <= 1.0:
        raise ModelDomainError(f"shared weight must lie in [0, 1], got {shared_weight!r}")
    return ParameterBlock(
        names=tuple(c.name for c in ordered),
        mean=tuple(c.mean for c in ordered),
        covariance=tuple(tuple(ordered[i].variance if i == j else 0.0 for j in range(n)) for i in range(n)),
        ea_lower_j_per_mol=ea_lower,
        shared_weight=shared_weight,
        estimates=tuple(c.estimate for c in ordered),
    )


def family_priors(reaction_class: ReactionClassDoc, family: DoseFormFamily) -> FamilyPriors:
    """The class priors for a dose-form family (``ConfigReferenceError`` when missing)."""
    for entry in reaction_class["priors"]:
        if entry["dose_form_family"] == family:
            return entry
    raise ConfigReferenceError(
        "reaction-classes", [f"{reaction_class['class_id']!r} has no priors for family {family!r}"]
    )


ANCHOR_FALLBACK: Mapping[DoseFormFamily, DoseFormFamily] = {"semisolid": "solid", "suspension": "solution"}
"""Anchor family fallbacks of spec 7.3.1 (structural)."""


def select_anchor(reaction_class: ReactionClassDoc, family: DoseFormFamily) -> Anchor | None:
    """The class anchor whose dose-form family equals the product's, first in config
    order; semisolid falls back to solid and suspension to solution (spec 7.3.1)."""
    for wanted in (family, ANCHOR_FALLBACK.get(family)):
        if wanted is None:
            continue
        for anchor in reaction_class["anchors"]:
            if anchor["dose_form_family"] == wanted:
                return anchor
    return None


def total_rate_prior(bundle: ConfigBundle, family: DoseFormFamily) -> TotalRatePrior:
    """The product total-rate prior for a dose-form family (spec 9.5)."""
    for entry in bundle.reaction_classes["total_rate_priors"]:
        if entry["dose_form_family"] == family:
            return entry
    raise ConfigReferenceError("reaction-classes", [f"no total_rate_priors entry for family {family!r}"])


def _degradant(
    reaction_class_ids: Sequence[str],
    classes: Mapping[str, ReactionClassDoc],
    ingredient: ActiveIngredientDoc,
) -> DegradantSpec | None:
    """Product degradants override the class's generic degradant for the same class
    (spec 5.6); the first class (in the order given) with a degradant wins."""
    for class_id in reaction_class_ids:
        for product_degradant in ingredient["degradants"]:
            if product_degradant["formed_by_class_id"] == class_id:
                mass = product_degradant["molar_mass_g_per_mol"]
                limit = product_degradant["limit_percent"]
                return DegradantSpec(
                    degradant_id=product_degradant["degradant_id"],
                    name=product_degradant["name"],
                    smiles=product_degradant["smiles"],
                    formed_by_class_id=class_id,
                    yield_mol_per_mol=product_degradant["yield_mol_per_mol"]["value"],
                    molar_mass_g_per_mol=None if mass is None else mass["value"],
                    limit_percent=None if limit is None else limit["value"],
                    toxicity_flag=product_degradant["toxicity_flag"],
                    product_specific=True,
                    source_ids=tuple(sorted(set(product_degradant["source_ids"]))),
                )
        generic = classes[class_id]["degradant"] if class_id in classes else None
        if generic is not None:
            mass = generic["molar_mass_g_per_mol"]
            return DegradantSpec(
                degradant_id=generic["degradant_id"],
                name=generic["name"],
                smiles=generic["smiles"],
                formed_by_class_id=class_id,
                yield_mol_per_mol=generic["yield_mol_per_mol"]["value"],
                molar_mass_g_per_mol=None if mass is None else mass["value"],
                limit_percent=None,
                toxicity_flag=generic["toxicity_flag"],
                product_specific=False,
                source_ids=tuple(sorted(set(generic["source_ids"]))),
            )
    return None


def _class_range(reaction_class: ReactionClassDoc) -> ValidatedRange:
    vr = reaction_class["validated_range"]
    return ValidatedRange(
        temperature_min_k=vr["temperature_min_k"],
        temperature_max_k=vr["temperature_max_k"],
        rh_min_percent=vr["rh_min_percent"],
        rh_max_percent=vr["rh_max_percent"],
        source_layer="class_prior",
        source_ids=tuple(sorted(set(vr["source_ids"]))),
    )


# =========================================================================================
# Pathway resolution
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Context:
    bundle: ConfigBundle
    product: ProductDoc
    ingredient: ActiveIngredientDoc
    family: DoseFormFamily
    reference_temperature_k: float
    reference_rh_percent: float
    classes: Mapping[str, ReactionClassDoc]
    cache_dir: Path | None


@dataclasses.dataclass(frozen=True, slots=True)
class _LnK:
    """The resolved ln k* of one thermal pathway."""

    component: _Component
    shared_weight: float
    computed: ComputedDetail | None
    zone_correction: ZoneCorrection | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SplitMember:
    """One pathway of the total-rate split (spec 9.5): prior mean ``mu_ln_k`` and standard
    deviation ``sd_ln_k`` (s_p for every member). An anchored member (computed layer) has
    mu_total - ln N and no share or free share. A free member has its share pi_i and, in a
    free group of two or more, ``free_share`` (delta_i, s_m,i) for the draw-time log-softmax
    perturbation. ``shift`` is the Evans-Polanyi ``ComputedDetail`` (None when unshifted)."""

    pathway_id: str
    anchored: bool
    share: float | None
    mu_ln_k: float
    sd_ln_k: float
    free_share: FreeShare | None
    shift: ComputedDetail | None


@dataclasses.dataclass(frozen=True, slots=True)
class TotalRateSplit:
    """The total-rate split of one ingredient (spec 9.5): s_p and, for the free members of a
    shifted split, the provenance of the redistribution (the total-rate prior plus the
    Evans-Polanyi entries and method errors of the shifted members)."""

    total: TotalRatePrior
    members: tuple[SplitMember, ...]
    sd_class_prior: float
    source_ids: tuple[str, ...]
    basis: Basis

    @property
    def n_free(self) -> int:
        """N_F, the number of free (unanchored) members."""
        return sum(1 for member in self.members if not member.anchored)

    @property
    def shifted(self) -> bool:
        """Whether any member carries an Evans-Polanyi shift."""
        return any(member.shift is not None for member in self.members)

    def member(self, pathway_id: str) -> SplitMember:
        for candidate in self.members:
            if candidate.pathway_id == pathway_id:
                return candidate
        raise ModelDomainError(f"pathway {pathway_id!r} is not in the total-rate split")


@dataclasses.dataclass(frozen=True, slots=True)
class _PriorMean:
    """The class prior of ln k* (mean and standard deviation) that 9.4 combines with a
    computed estimate."""

    mu: float
    sd: float
    source_ids: tuple[str, ...]
    basis: Basis
    method: str
    note: str
    in_split: bool
    shift: ComputedDetail | None


def _split(ctx: _Context, members: Sequence[PathwayMatch]) -> TotalRateSplit | None:
    """The spec 9.5 split over ``members``: members with an anchor are anchored, the others
    form the free group F. With N_F >= 2 every free member whose class has an Evans-Polanyi
    entry gets its shift (7.3.4); a free group of one has nothing to redistribute, so no
    shift is computed for it. None without members."""
    if not members:
        return None
    total = total_rate_prior(ctx.bundle, ctx.family)
    settings = ctx.bundle.settings["computed_layer"]
    anchored = {m.pathway_id for m in members if select_anchor(m.reaction_class, ctx.family) is not None}
    free = [m for m in members if m.pathway_id not in anchored]
    sources = list(total["ln_k_ref_per_s"]["source_ids"])
    bases: list[Basis] = [total["ln_k_ref_per_s"]["basis"]]
    shifts: dict[str, ComputedDetail] = {}
    if len(free) >= 2:
        for match in free:
            entry = lfer.evans_polanyi_entry(ctx.bundle.lfer, match.reaction_class["class_id"])
            if entry is None:
                continue
            shift = lfer.evans_polanyi_prior_shift(
                ctx.ingredient["smiles"],
                match.sites,
                match.reaction_class,
                ctx.bundle.lfer,
                settings,
                ctx.reference_temperature_k,
                ctx.cache_dir,
            )
            if shift is None:  # pragma: no cover - the entry exists, so a shift is returned
                continue
            shifts[match.pathway_id] = shift
            method_error = match.reaction_class["computed_method"]["method_error_sd_log10"]
            for value in (entry["alpha"], entry["max_abs_delta_bde_kj_per_mol"], method_error):
                sources.extend(value["source_ids"])
                bases.append(value["basis"])
    deltas = [shifts[m.pathway_id].delta_ln_k if m.pathway_id in shifts else 0.0 for m in free]
    anchored_mu, s_p, priors = split_class_priors(total, len(anchored), deltas)
    free_priors = {m.pathway_id: prior for m, prior in zip(free, priors, strict=True)}
    out: list[SplitMember] = []
    for match in members:
        if match.pathway_id in anchored:
            out.append(SplitMember(match.pathway_id, True, None, anchored_mu, s_p, None, None))
            continue
        prior = free_priors[match.pathway_id]
        shift = shifts.get(match.pathway_id)
        free_share = None
        if len(free) >= 2:
            # Spec 9.5: s_m,i is the shift's method error (lfer sets 0 when no site was inside
            # the applicability domain); delta_i, s_m,i and pi_i go to the trace.
            free_share = FreeShare(
                delta_ln=0.0 if shift is None else shift.delta_ln_k,
                method_sd_ln=0.0 if shift is None else shift.sd_ln_k,
            )
            if shift is not None:
                shift = dataclasses.replace(
                    shift,
                    descriptors=(
                        *shift.descriptors,
                        ("split_delta_ln", free_share.delta_ln),
                        ("split_method_sd_ln", free_share.method_sd_ln),
                        ("split_share", prior.share),
                    ),
                )
        out.append(SplitMember(match.pathway_id, False, prior.share, prior.mu_ln_k, s_p, free_share, shift))
    return TotalRateSplit(
        total=total,
        members=tuple(out),
        sd_class_prior=s_p,
        source_ids=tuple(sorted(set(sources))),
        basis=weakest_basis(*bases),
    )


def _thermal_ln_k(
    ctx: _Context,
    match: PathwayMatch,
    prior: _PriorMean,
    ea: _Component,
    b: _Component | None,
) -> _LnK:
    """ln k* by the hierarchy below measured models (spec 9.1, 9.4, 9.5): computed layer
    against an anchor combined with the class prior, else the class prior (for a free split
    member, mu_p,i and s_post,i of the redistribution). ``prior.in_split`` marks pathways of
    the total-rate split (shared weight w); the others (racemization) have shared weight 0."""
    reaction_class = match.reaction_class
    prior_mu, prior_sd = prior.mu, prior.sd
    settings = ctx.bundle.settings["computed_layer"]
    anchor = select_anchor(reaction_class, ctx.family)
    method_error = reaction_class["computed_method"]["method_error_sd_log10"]
    if anchor is not None:
        detail = lfer.computed_adjustment(
            ctx.ingredient["smiles"],
            match.sites,
            reaction_class,
            anchor,
            ctx.bundle.lfer,
            settings["same_molecule_sd_log10"]["value"],
            settings["unparameterized_substituent_sd_log10"]["value"],
            settings["ortho_substituent_sd_log10"]["value"],
            settings["heteroaromatic_ring_sd_log10"]["value"],
            xtb_settings=settings,
            reference_temperature_k=ctx.reference_temperature_k,
            cache_dir=ctx.cache_dir,
        )
        b_mean = 0.0 if b is None else b.mean
        b_sd = 0.0 if b is None else math.sqrt(b.variance)
        mu_a, sd_a = anchor_at_reference(
            anchor,
            ea.mean,
            math.sqrt(ea.variance),
            b_mean,
            b_sd,
            ctx.reference_temperature_k,
            ctx.reference_rh_percent,
            bundle=ctx.bundle,
        )
        mu_c = mu_a + detail.delta_ln_k
        sd_c = math.hypot(sd_a, detail.sd_ln_k)
        mu_post, sd_post = precision_weighted(prior_mu, prior_sd, mu_c, sd_c)
        zone_prior = _anchor_zone_prior(anchor, ctx.bundle)
        zone_mean, zone_sd = _zone_ln_moments(zone_prior)
        correction = (
            ZoneCorrection(anchor["storage_zone_id"], (sd_post / sd_c) ** 2, zone_sd)
            if zone_sd > 0.0
            else None
        )
        error_basis = (
            settings["same_molecule_sd_log10"]["basis"]
            if "same_molecule" in detail.flags
            else method_error["basis"]
        )
        basis = weakest_basis(
            prior.basis,
            anchor["ln_k_per_s"]["basis"],
            anchor["temperature_k"]["basis"],
            anchor["rh_percent"]["basis"],
            zone_prior["basis"],
            error_basis,
        )
        sources = [
            *prior.source_ids,
            *anchor["ln_k_per_s"]["source_ids"],
            *anchor["temperature_k"]["source_ids"],
            *anchor["rh_percent"]["source_ids"],
            *zone_prior["source_ids"],
            *ea.estimate.source_ids,
            *([] if b is None else b.estimate.source_ids),
        ]
        context = anchor.get("storage_context")
        if context is not None:
            sources.extend(context["source_ids"])
            basis = weakest_basis(basis, context["basis"])
        note = (
            f"anchor {anchor['anchor_id']} ground-equivalent at (T*, RH*): {mu_a:.6g} +- {sd_a:.6g}; "
            f"package {anchor['package_id']}, zone {anchor['storage_zone_id']} ln multiplier "
            f"{zone_mean:.6g} +- {zone_sd:.6g} removed; {detail.method} delta "
            f"{detail.delta_ln_k:.6g} +- {detail.sd_ln_k:.6g}; computed {mu_c:.6g} +- {sd_c:.6g} precision-"
            f"weighted with {prior.method} {prior_mu:.6g} +- {prior_sd:.6g} ({prior.note})"
        )
        estimate = _estimate(
            "ln_k_ref_per_s",
            "normal",
            mu_post,
            sd_post,
            "computed",
            f"computed:{detail.method}",
            sources,
            basis,
            note,
        )
        weight = (sd_post * sd_post) / (prior_sd * prior_sd) if prior.in_split else 0.0
        return _LnK(
            _Component("ln_k_ref_per_s", mu_post, sd_post * sd_post, estimate), weight, detail, correction
        )

    if (
        not prior.in_split
        and lfer.evans_polanyi_entry(ctx.bundle.lfer, reaction_class["class_id"]) is not None
    ):
        raise ModelDomainError(
            f"class {reaction_class['class_id']!r} has an Evans-Polanyi entry but no anchor and is "
            "outside the total-rate split (spec 9.5)"
        )
    variance = prior_sd * prior_sd
    estimate = _estimate(
        "ln_k_ref_per_s",
        "normal",
        prior_mu,
        prior_sd,
        "class_prior",
        prior.method,
        prior.source_ids,
        prior.basis,
        prior.note,
    )
    # Spec 9.5: w = 1 for every unanchored split member (a free member's log-share
    # perturbation is added at draw time), 0 outside the split.
    weight = 1.0 if prior.in_split else 0.0
    return _LnK(_Component("ln_k_ref_per_s", prior_mu, variance, estimate), weight, prior.shift)


def _thermal_pathway(ctx: _Context, match: PathwayMatch, split: TotalRateSplit | None) -> Pathway:
    reaction_class = match.reaction_class
    label = f"{reaction_class['class_id']} ({ctx.family})"
    priors = family_priors(reaction_class, ctx.family)
    ea, ea_lower = _ea_component(priors["ea_j_per_mol"], label)
    components: list[_Component] = [ea]
    b: _Component | None = None
    if priors["b_per_percent_rh"] is not None:
        b = _linear_component("b_per_percent_rh", priors["b_per_percent_rh"], label)
        components.append(b)
    if priors["frozen_rate_multiplier"] is not None:
        components.append(
            _log_component(
                "ln_frozen_rate_multiplier", priors["frozen_rate_multiplier"], label, "class_prior"
            )
        )
    if split is not None:
        member = split.member(match.pathway_id)
        total = split.total
        prior_total = total["ln_k_ref_per_s"]
        n = len(split.members)
        redistributed = split.shifted and not member.anchored
        family_label = total["dose_form_family"]
        if redistributed and member.free_share is not None:
            note = (
                f"product total-rate prior ({family_label}): the free group (N_F = {split.n_free} of "
                f"N = {n} class-based destructive thermal pathways) redistributes its part N_F/N by the "
                "Evans-Polanyi shifts, mu_total + ln(N_F/N) + delta_i - ln(sum_F exp(delta_j)) with "
                f"delta_i = {member.free_share.delta_ln:.6g}, s_m,i = {member.free_share.method_sd_ln:.6g} "
                f"and pi_i = {member.share}; block sd s_p and w = 1, the log-share perturbation is added "
                f"at draw time (spec 9.5). {prior_total['note']}"
            )
        else:
            anchored_note = "; anchored, so no shift moves it" if member.anchored and split.shifted else ""
            note = (
                f"product total-rate prior ({family_label}) split equally among N = {n} class-based "
                f"destructive thermal pathways: mu - ln N{anchored_note}. {prior_total['note']}"
            )
        resolved = _thermal_ln_k(
            ctx,
            match,
            _PriorMean(
                mu=member.mu_ln_k,
                sd=member.sd_ln_k,
                source_ids=split.source_ids if redistributed else tuple(prior_total["source_ids"]),
                basis=split.basis if redistributed else prior_total["basis"],
                method="total_rate_split+evans_polanyi_bde" if redistributed else "total_rate_split",
                note=note,
                in_split=True,
                shift=member.shift,
            ),
            ea,
            b,
        )
    else:
        class_prior = priors["ln_k_ref_per_s"]
        if class_prior is None or class_prior["family"] != "normal":
            raise ConfigReferenceError(
                "reaction-classes",
                [f"{label}: a pathway outside the total-rate split needs a normal ln k prior"],
            )
        resolved = _thermal_ln_k(
            ctx,
            match,
            _PriorMean(
                mu=class_prior["mu"],
                sd=class_prior["sigma"],
                source_ids=tuple(class_prior["source_ids"]),
                basis=class_prior["basis"],
                method="class_prior",
                note=f"class ln k prior. {class_prior['note']}",
                in_split=False,
                shift=None,
            ),
            ea,
            b,
        )
    components.append(resolved.component)
    degradant = (
        _degradant([reaction_class["class_id"]], ctx.classes, ctx.ingredient)
        if reaction_class["role"] == "destructive"
        else None
    )
    anchor = select_anchor(reaction_class, ctx.family)
    identification = DegradantIdentification()
    if (
        anchor is not None
        and degradant is not None
        and degradant.degradant_id in anchor.get("measured_degradant_ids", [])
    ):
        identification = DegradantIdentification(
            identified=True,
            evidence_kind="anchor",
            evidence_id=anchor["anchor_id"],
            source_ids=tuple(sorted(set(anchor["ln_k_per_s"]["source_ids"]))),
        )
    return Pathway(
        pathway_id=match.pathway_id,
        ingredient_id=match.ingredient_id,
        reaction_class_id=reaction_class["class_id"],
        mechanism=reaction_class["mechanism"],
        driver=reaction_class["driver"],
        role=reaction_class["role"],
        sites=match.sites,
        parameters=_block(components, ea_lower, resolved.shared_weight),
        validated_range=_class_range(reaction_class),
        degradant=degradant,
        computed=resolved.computed,
        free_share=None if split is None else split.member(match.pathway_id).free_share,
        zone_correction=resolved.zone_correction,
        degradant_identification=identification,
    )


def _light_pathway(ctx: _Context, match: PathwayMatch) -> Pathway:
    reaction_class = match.reaction_class
    light = reaction_class["light_priors"]
    label = reaction_class["class_id"]
    if light is None:
        raise ConfigReferenceError("reaction-classes", [f"{label}: a light-driven class needs light_priors"])
    components = [
        _log_component("ln_k_vis_per_lux_h", light["k_vis_per_lux_h"], f"{label} visible", "light_prior"),
        _log_component(
            "ln_k_uv_per_w_h_per_m2", light["k_uv_per_w_h_per_m2"], f"{label} near UV", "light_prior"
        ),
    ]
    return Pathway(
        pathway_id=match.pathway_id,
        ingredient_id=match.ingredient_id,
        reaction_class_id=label,
        mechanism=reaction_class["mechanism"],
        driver=reaction_class["driver"],
        role=reaction_class["role"],
        sites=match.sites,
        parameters=_block(components, NO_EA_LOWER_BOUND, 0.0),
        validated_range=_class_range(reaction_class),
        degradant=_degradant([label], ctx.classes, ctx.ingredient),
        computed=None,
    )


def _ln_normal_component(name: ParameterName, value: LnNormal, method: str) -> _Component:
    estimate = _estimate(
        name,
        "normal",
        value.mu,
        value.sigma,
        "class_prior",
        method,
        value.source_ids,
        value.basis,
        value.note,
    )
    return _Component(name, value.mu, value.sigma * value.sigma, estimate)


def _radiolysis_pathway(ctx: _Context, match: PathwayMatch) -> Pathway:
    reaction_class = match.reaction_class
    concentration = ctx.ingredient["concentration_mol_per_kg"]
    prior = radiolysis_prior(
        ctx.bundle.radiation,
        ctx.family,
        ctx.ingredient["molar_mass_g_per_mol"]["value"],
        None if concentration is None else concentration["value"],
    )
    components = [_ln_normal_component("ln_k_dose_per_gy", prior.ln_k_dose_per_gy, prior.method)]
    descriptors: list[tuple[str, float]] = [
        ("molar_mass_g_per_mol", prior.molar_mass_g_per_mol),
        ("ln_k_dose_per_gy_mu", prior.ln_k_dose_per_gy.mu),
        ("ln_k_dose_per_gy_sigma", prior.ln_k_dose_per_gy.sigma),
    ]
    if prior.ln_k_dose_frozen_per_gy is not None:
        components.append(
            _ln_normal_component("ln_k_dose_frozen_per_gy", prior.ln_k_dose_frozen_per_gy, "g_value_direct")
        )
        descriptors.append(("ln_k_dose_frozen_per_gy_mu", prior.ln_k_dose_frozen_per_gy.mu))
    if prior.concentration_mol_per_kg is not None and prior.half_scavenging_mol_per_kg is not None:
        descriptors.append(("concentration_mol_per_kg", prior.concentration_mol_per_kg))
        descriptors.append(("half_scavenging_concentration_mol_per_kg", prior.half_scavenging_mol_per_kg))
    computed = ComputedDetail(
        method=prior.method,
        anchor_id=None,
        delta_ln_k=0.0,
        sd_ln_k=0.0,
        flags=("class_prior",),
        descriptors=tuple(descriptors),
        cache_keys=(),
    )
    return Pathway(
        pathway_id=match.pathway_id,
        ingredient_id=match.ingredient_id,
        reaction_class_id=reaction_class["class_id"],
        mechanism=reaction_class["mechanism"],
        driver=reaction_class["driver"],
        role=reaction_class["role"],
        sites=match.sites,
        parameters=_block(components, NO_EA_LOWER_BOUND, 0.0),
        validated_range=_class_range(reaction_class),
        degradant=_degradant([reaction_class["class_id"]], ctx.classes, ctx.ingredient),
        computed=computed,
    )


# =========================================================================================
# Measured models (spec 9.1 layer 1)
# =========================================================================================

MEASURED_THERMAL_NAMES: frozenset[ParameterName] = frozenset(
    {"ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh", "ln_frozen_rate_multiplier"}
)


def select_measured_model(
    bundle: ConfigBundle, product_id: str, ingredient_id: str, package_ids: Sequence[str]
) -> StabilityModelDoc | None:
    """The product-scope model for (product, ingredient) whose ``package_id`` is null or
    equals the package of every segment (spec 9.1). A package-specific model is preferred
    over a package-independent one, then config order. Reference-study models are never
    product models."""
    packages = set(package_ids)
    candidates = [
        model
        for model in bundle.stability_models["models"]
        if model["scope"] == "product"
        and model["product_id"] == product_id
        and model["ingredient_id"] == ingredient_id
        and (model["package_id"] is None or packages == {model["package_id"]})
    ]
    specific = [m for m in candidates if m["package_id"] is not None]
    if specific:
        return specific[0]
    return candidates[0] if candidates else None


def _measured_pathway(
    ctx: _Context, model: StabilityModelDoc, replaced: Sequence[PathwayMatch]
) -> tuple[Pathway, RateLawKind]:
    """The pathway that replaces the destructive thermal and covered pathways (spec 9.1): id,
    labels, Ea bound, sites and degradant as 9.1 lists them."""
    names = list(model["parameters"]["names"])
    unknown = [name for name in names if name not in MEASURED_THERMAL_NAMES]
    if unknown:
        raise ModelDomainError(f"measured model {model['model_id']!r} has non-thermal parameters {unknown}")
    reference_rh = model["reference_rh_percent"]
    if reference_rh is None and "b_per_percent_rh" in names:
        raise ConfigReferenceError(
            "stability-models", [f"{model['model_id']!r} has B but no reference_rh_percent"]
        )
    from_rh = ctx.reference_rh_percent if reference_rh is None else reference_rh
    mean, covariance = convert_reference(
        names,
        model["parameters"]["mean"],
        model["parameters"]["covariance"],
        model["reference_temperature_k"],
        from_rh,
        ctx.reference_temperature_k,
        ctx.reference_rh_percent,
    )
    for i, name in enumerate(names):
        if covariance[i][i] < 0.0:
            raise ModelDomainError(f"measured model {model['model_id']!r}: negative variance of {name}")
    first = replaced[0].reaction_class
    ea_lower = NO_EA_LOWER_BOUND
    if "ea_j_per_mol" in names:
        ea_prior = family_priors(first, ctx.family)["ea_j_per_mol"]
        if ea_prior is None or ea_prior["lower"] is None:
            raise ModelDomainError(f"{first['class_id']!r}: no Ea lower bound for the measured block")
        ea_lower = ea_prior["lower"]
    method = f"measured_model:{model['model_id']}"
    estimates = []
    for i, name in enumerate(names):
        is_ea = name == "ea_j_per_mol"
        estimates.append(
            _estimate(
                name,
                "truncated_normal" if is_ea else "normal",
                mean[i],
                math.sqrt(covariance[i][i]),
                "measured_model",
                method,
                model["source_ids"],
                model["basis"],
                f"{model['model_id']} converted from ({model['reference_temperature_k']} K, {from_rh} %RH) "
                f"to (T*, RH*) by the exact linear map (spec 9.3)",
                lower=ea_lower if is_ea else None,
            )
        )
    order = sorted(range(len(names)), key=lambda i: PARAMETER_ORDER.index(names[i]))
    block = ParameterBlock(
        names=tuple(names[i] for i in order),
        mean=tuple(mean[i] for i in order),
        covariance=tuple(tuple(covariance[i][j] for j in order) for i in order),
        ea_lower_j_per_mol=ea_lower,
        shared_weight=0.0,
        estimates=tuple(estimates[i] for i in order),
    )
    vr = model["validated_range"]
    sites: list[ReactiveSite] = sorted(
        (site for match in replaced for site in match.sites), key=lambda s: (s.site_atom, s.class_id)
    )
    degradant = _degradant([m.reaction_class["class_id"] for m in replaced], ctx.classes, ctx.ingredient)
    identification = DegradantIdentification()
    if degradant is not None and degradant.degradant_id in model.get("measured_degradant_ids", []):
        identification = DegradantIdentification(
            identified=True,
            evidence_kind="measured_model",
            evidence_id=model["model_id"],
            source_ids=tuple(sorted(set(model["source_ids"]))),
        )
    pathway = Pathway(
        pathway_id=f"{ctx.ingredient['ingredient_id']}:{model['model_id']}",
        ingredient_id=ctx.ingredient["ingredient_id"],
        reaction_class_id=model["model_id"],
        mechanism=first["mechanism"],
        driver="thermal",
        role="destructive",
        sites=tuple(sites),
        parameters=block,
        validated_range=ValidatedRange(
            temperature_min_k=vr["temperature_min_k"],
            temperature_max_k=vr["temperature_max_k"],
            rh_min_percent=vr["rh_min_percent"],
            rh_max_percent=vr["rh_max_percent"],
            source_layer="measured_model",
            source_ids=tuple(sorted(set(vr["source_ids"]))),
        ),
        degradant=degradant,
        computed=None,
        degradant_identification=identification,
    )
    return pathway, model["rate_law_kind"]


# =========================================================================================
# The model
# =========================================================================================


def _product(bundle: ConfigBundle, product_id: str) -> ProductDoc:
    for product in bundle.formulary["products"]:
        if product["product_id"] == product_id:
            return product
    raise ConfigReferenceError("formulary", [f"unknown product {product_id!r}"])


def _ingredient(product: ProductDoc, ingredient_id: str) -> ActiveIngredientDoc:
    for ingredient in product["ingredients"]:
        if ingredient["ingredient_id"] == ingredient_id:
            return ingredient
    raise ConfigReferenceError(
        "formulary", [f"{product['product_id']!r} has no ingredient {ingredient_id!r}"]
    )


def _packages(bundle: ConfigBundle, package_ids: Sequence[str]) -> tuple[PackageDoc, ...]:
    if not package_ids:
        raise InputError("a stability model needs the packages of the lot history (none given)")
    known = {p["package_id"]: p for p in bundle.packages["packages"]}
    missing = sorted({pid for pid in package_ids if pid not in known})
    if missing:
        raise ConfigReferenceError("packages", [f"unknown package {pid!r}" for pid in missing])
    return tuple(known[pid] for pid in sorted(set(package_ids)))


def _default_kind(bundle: ConfigBundle, family: DoseFormFamily) -> RateLawKind:
    for entry in bundle.settings["rate_law_defaults"]:
        if entry["dose_form_family"] == family:
            return entry["kind"]
    raise ConfigReferenceError("engine-settings", [f"no rate_law_defaults entry for family {family!r}"])


@dataclasses.dataclass(frozen=True, slots=True)
class _Resolution:
    ctx: _Context
    packages: tuple[PackageDoc, ...]
    measured: StabilityModelDoc | None
    replaced: tuple[PathwayMatch, ...]
    remaining: tuple[PathwayMatch, ...]
    split_members: tuple[PathwayMatch, ...]


def _resolve(
    bundle: ConfigBundle,
    product_id: str,
    ingredient_id: str,
    package_ids: Sequence[str],
    cache_dir: Path | None,
) -> _Resolution:
    product = _product(bundle, product_id)
    ingredient = _ingredient(product, ingredient_id)
    packages = _packages(bundle, package_ids)
    reference = bundle.settings["reference_conditions"]
    ctx = _Context(
        bundle=bundle,
        product=product,
        ingredient=ingredient,
        family=product["dose_form_family"],
        reference_temperature_k=reference["temperature_k"]["value"],
        reference_rh_percent=reference["relative_humidity_percent"]["value"],
        classes={c["class_id"]: c for c in bundle.reaction_classes["classes"]},
        cache_dir=cache_dir,
    )
    matches = identify_pathways(product, ingredient, bundle.reaction_classes, packages)
    measured = select_measured_model(bundle, product_id, ingredient_id, [p["package_id"] for p in packages])
    replaced: list[PathwayMatch] = []
    if measured is not None:
        covered = set(measured["covers"])
        replaced = [
            m
            for m in matches
            if (m.reaction_class["driver"] == "thermal" and m.reaction_class["role"] == "destructive")
            or m.reaction_class["class_id"] in covered
        ]
    remaining = tuple(m for m in matches if m not in replaced)
    return _Resolution(
        ctx=ctx,
        packages=packages,
        measured=measured,
        replaced=tuple(replaced),
        remaining=remaining,
        split_members=tuple(
            m
            for m in remaining
            if m.reaction_class["driver"] == "thermal" and m.reaction_class["role"] == "destructive"
        ),
    )


def total_rate_split(
    bundle: ConfigBundle,
    product_id: str,
    ingredient_id: str,
    package_ids: Sequence[str],
    *,
    cache_dir: Path | None = None,
) -> TotalRateSplit | None:
    """The spec 9.5 total-rate split that ``build_stability_model`` uses for the same
    arguments (None when the ingredient has no class-based destructive thermal pathway,
    for example under a measured model). Same errors as ``build_stability_model``."""
    resolution = _resolve(bundle, product_id, ingredient_id, package_ids, cache_dir)
    return _split(resolution.ctx, resolution.split_members)


def build_stability_model(
    bundle: ConfigBundle,
    product_id: str,
    ingredient_id: str,
    package_ids: Sequence[str],
    *,
    cache_dir: Path | None = None,
) -> StabilityModel:
    """Resolve every pathway's ParameterBlock and provenance for one ingredient of one
    product over the packages the lot has been in (spec 9.1 to 9.6). Deterministic;
    xTB results come from the cache or are computed and cached (spec 7.4).

    ``cache_dir`` (keyword only) overrides the BDE cache directory. Otherwise use
    ``PHARMACY_XTB_CACHE_DIR``, then ``data/pharmacy/xtb-cache``. Pathways are sorted by
    pathway_id (the model order of spec 9.7). Raises ``ConfigReferenceError`` (unknown
    product, ingredient or package, missing priors), ``InputError`` (no packages),
    ``ModelDomainError`` (unusable priors),
    ``StructureError`` and the xTB errors of ``chemistry.bde``."""
    resolution = _resolve(bundle, product_id, ingredient_id, package_ids, cache_dir)
    ctx = resolution.ctx
    product = ctx.product
    ingredient = ctx.ingredient
    family = ctx.family
    measured = resolution.measured

    pathways: list[Pathway] = []
    kind = _default_kind(bundle, family)
    order_n: float | None = None
    alpha0: float | None = None
    avrami_n: float | None = None
    if measured is not None:
        measured_pathway, kind = _measured_pathway(ctx, measured, resolution.replaced)
        pathways.append(measured_pathway)
        params = measured["kind_parameters"]
        order_n, alpha0, avrami_n = params["order_n"], params["alpha0"], params["avrami_n"]

    split = _split(ctx, resolution.split_members)
    for match in resolution.remaining:
        driver = match.reaction_class["driver"]
        if driver == "thermal":
            pathways.append(
                _thermal_pathway(ctx, match, split if match in resolution.split_members else None)
            )
        elif driver == "light":
            pathways.append(_light_pathway(ctx, match))
        else:
            pathways.append(_radiolysis_pathway(ctx, match))

    needed = KINDS_NEEDING_PARAMETERS.get(kind)
    values = {"order_n": order_n, "alpha0": alpha0, "avrami_n": avrami_n}
    if needed is not None and values[needed] is None:
        raise ModelDomainError(f"rate-law kind {kind} needs {needed}")
    racemization = ingredient["racemization"]
    freezing = product["freezing_point_k"]
    thresholds = product["regime_thresholds"]
    phase = thresholds["phase_change_temperature_k"]
    deliquescence = thresholds["deliquescence_rh_percent"]
    sources = {*product["source_ids"], *ingredient["source_ids"]}
    if measured is not None:
        sources.update(measured["source_ids"])
    return StabilityModel(
        model_id=f"{product_id}:{ingredient_id}:{'+'.join(p['package_id'] for p in resolution.packages)}",
        product_id=product_id,
        ingredient_id=ingredient_id,
        dose_form_family=family,
        rate_law_kind=kind,
        order_n=order_n,
        alpha0=alpha0,
        avrami_n=avrami_n,
        reference_temperature_k=ctx.reference_temperature_k,
        reference_rh_percent=ctx.reference_rh_percent,
        pathways=tuple(sorted(pathways, key=lambda p: p.pathway_id)),
        measured_model_id=None if measured is None else measured["model_id"],
        max_study_duration_s=None if measured is None else measured["max_study_duration_s"],
        initial_active_fraction=1.0
        if racemization is None
        else racemization["initial_active_fraction"]["value"],
        freezing_point_k=None if freezing is None else freezing["value"],
        phase_change_temperature_k=None if phase is None else phase["value"],
        deliquescence_rh_percent=None if deliquescence is None else deliquescence["value"],
        source_ids=tuple(sorted(sources)),
    )

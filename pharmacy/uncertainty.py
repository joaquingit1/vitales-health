"""Uncertainty: seeds, variate layout, parameter draws and the JCGM 101 adaptive loop.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 9.
"""

import dataclasses
import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal

import numpy as np
from scipy.special import ndtr, ndtri

from pharmacy.config import canonical_json_bytes
from pharmacy.errors import ComputationError, InputError, ModelDomainError
from pharmacy.kinetics import DrawBlock, FloatArray, ln_rate_multiplier
from pharmacy.types import (
    CoverageInterval,
    JSONValue,
    MonteCarloSettings,
    ParameterName,
    Pathway,
    QuantileValue,
    StabilityModel,
    StorageZoneDoc,
)
from pharmacy.update import effective_sample_size, normalized_weights

_SEED_BYTES: Final[int] = 16
"""The seed is the first 16 bytes of the SHA-256 digest (spec 9.8)."""

_QUANTILE_SLACK: Final[float] = 1e-12
"""Cumulative weight tolerance of the type-1 inverse ECDF (spec 9.10)."""

_JCGM_MIN_BATCH: Final[int] = 10_000
"""JCGM 101 7.9.2: M = max(ceil(100 / (1 - p)), 10^4) (spec 9.9)."""

_JCGM_BATCH_NUMERATOR: Final[int] = 100
"""JCGM 101 7.9.2: J = ceil(100 / (1 - p)) (spec 9.9)."""

_SLOTS_PER_PATHWAY: Final[int] = 8
_SLOTS_PER_ZONE: Final[int] = 3
_HEAD_COLUMNS: Final[int] = 2
_SLOT: Final[Mapping[ParameterName, int]] = {
    "ln_k_ref_per_s": 0,
    "b_per_percent_rh": 1,
    "ln_k_vis_per_lux_h": 2,
    "ln_k_uv_per_w_h_per_m2": 3,
    "ln_k_dose_per_gy": 4,
    "ln_k_dose_frozen_per_gy": 4,
    "ln_frozen_rate_multiplier": 5,
}
"""Variate slot of each parameter inside a pathway's 8 columns (spec 9.7); slots 6 and 7
are z_T and z_RH of the widening (spec 8.2). Ea uses the pathway's uniform."""
_SLOT_Z_TEMPERATURE: Final[int] = 6
_SLOT_Z_HUMIDITY: Final[int] = 7


def derive_seed(material: JSONValue) -> int:
    """seed = int.from_bytes(SHA-256(canonical_json_bytes(material))[:16], "big") (spec 9.8).
    ``material`` is the seed-material object of spec 9.8 (pins without the seed itself, lot,
    ingredient, as_of and purpose)."""
    digest = hashlib.sha256(canonical_json_bytes(material)).digest()
    return int.from_bytes(digest[:_SEED_BYTES], "big")


def seed_hex(seed: int) -> str:
    """The seed as 32 lower-case hex digits, big endian (spec 9.8)."""
    if seed < 0 or seed >= 1 << (8 * _SEED_BYTES):
        raise InputError(f"seed out of range: {seed!r}")
    return seed.to_bytes(_SEED_BYTES, "big").hex()


def batch_generator(seed: int, batch_index: int) -> np.random.Generator:
    """Generator(PCG64(SeedSequence(seed, spawn_key=(batch_index,)))) (spec 9.8)."""
    if seed < 0 or batch_index < 0:
        raise InputError(f"seed and batch index must be non-negative, got {seed!r}, {batch_index!r}")
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(batch_index,))))


def jcgm_batch_size(coverage_probability: float) -> int:
    """JCGM 101 7.9.2 batch size: M = max(J, 10^4) with J = ceil(100 / (1 - p)) (spec 9.9).
    Raises ``InputError`` unless 0 < p < 1."""
    if not (math.isfinite(coverage_probability) and 0.0 < coverage_probability < 1.0):
        raise InputError(f"coverage probability must lie in (0, 1), got {coverage_probability!r}")
    # The decimal value as written (0.9 is 9/10), so 1 - p does not pick up binary error.
    p = Fraction(repr(coverage_probability))
    j = math.ceil(Fraction(_JCGM_BATCH_NUMERATOR) / (1 - p))
    return max(j, _JCGM_MIN_BATCH)


def numerical_tolerance(standard_uncertainty: float, n_dig: int) -> float:
    """JCGM 101 7.9.2: write u with n_dig significant digits as c x 10^l; return
    delta = 0.5 x 10^l. u = 0 returns 0.0."""
    if not (math.isfinite(standard_uncertainty) and standard_uncertainty >= 0.0):
        raise InputError(
            f"the standard uncertainty must be finite and non-negative, got {standard_uncertainty!r}"
        )
    if n_dig < 1:
        raise InputError(f"n_dig must be at least 1, got {n_dig!r}")
    if standard_uncertainty == 0.0:
        return 0.0
    exact = Decimal(standard_uncertainty)
    exponent = exact.adjusted() - n_dig + 1
    digits = (exact.scaleb(-exponent)).to_integral_value()
    if digits >= 10**n_dig:
        exponent += 1
    return float(Decimal(5).scaleb(exponent - 1))


def _weights_of(values: FloatArray, weights: FloatArray | None) -> FloatArray | None:
    if weights is None:
        return None
    w = np.asarray(weights, dtype=np.float64)
    if w.shape != values.shape:
        raise InputError("weights and values must have the same shape")
    if not bool(np.all(np.isfinite(w))) or bool(np.any(w < 0.0)):
        raise InputError("weights must be finite and non-negative")
    total = float(np.sum(w))
    if not total > 0.0:
        raise InputError("weights must not all be zero")
    return np.asarray(w / total, dtype=np.float64)


def _values_of(values: FloatArray) -> FloatArray:
    v = np.asarray(values, dtype=np.float64)
    if v.ndim != 1 or v.size == 0:
        raise InputError("values must be a non-empty 1-D array")
    if bool(np.any(np.isnan(v))):
        raise InputError("values must not contain NaN")
    return v


def weighted_quantile(values: FloatArray, weights: FloatArray | None, probability: float) -> float:
    """Inverse weighted ECDF (type 1): sort values ascending (stable, ties keep input
    order), W_k = cumulative normalized weight; return the smallest value_k with
    W_k >= probability - 1e-12 (spec 9.10). ``weights`` None means equal weights."""
    v = _values_of(values)
    if not (math.isfinite(probability) and 0.0 <= probability <= 1.0):
        raise InputError(f"probability must lie in [0, 1], got {probability!r}")
    w = _weights_of(v, weights)
    order = np.argsort(v, kind="stable")
    cumulative = np.arange(1, v.size + 1, dtype=np.float64) / v.size if w is None else np.cumsum(w[order])
    index = int(np.searchsorted(cumulative, probability - _QUANTILE_SLACK, side="left"))
    return float(v[order[min(index, v.size - 1)]])


def coverage_interval(values: FloatArray, weights: FloatArray | None, probability: float) -> CoverageInterval:
    """Probabilistically symmetric coverage interval [q_{(1-p)/2}, q_{(1+p)/2}]."""
    if not (math.isfinite(probability) and 0.0 < probability < 1.0):
        raise InputError(f"coverage probability must lie in (0, 1), got {probability!r}")
    return CoverageInterval(
        probability=probability,
        low=weighted_quantile(values, weights, (1.0 - probability) / 2.0),
        high=weighted_quantile(values, weights, (1.0 + probability) / 2.0),
        kind="probabilistically_symmetric",
    )


def quantiles(
    values: FloatArray, weights: FloatArray | None, probabilities: Sequence[float]
) -> tuple[QuantileValue, ...]:
    """``weighted_quantile`` at each probability, in the order given."""
    return tuple(
        QuantileValue(probability=float(p), value=weighted_quantile(values, weights, p))
        for p in probabilities
    )


def _is_measured_block(pathway: Pathway) -> bool:
    """A measured model's block (spec 9.7): its ln k* estimate comes from the measured layer."""
    return any(
        estimate.name == "ln_k_ref_per_s" and estimate.source_layer == "measured_model"
        for estimate in pathway.parameters.estimates
    )


def _truncated_ea(mu: float, sigma: float, lower: float, u: FloatArray) -> FloatArray:
    """Ea = mu + sigma ndtri(Phi(a) + u (Phi(b) - Phi(a))) with a the standardized lower
    bound and no upper bound (spec 9.7)."""
    if sigma == 0.0:
        if mu < lower:
            raise ModelDomainError(f"a fixed Ea {mu!r} lies below its lower bound {lower!r}")
        return np.full(u.shape, mu, dtype=np.float64)
    phi_a = float(ndtr((lower - mu) / sigma)) if math.isfinite(lower) else 0.0
    return np.asarray(mu + sigma * ndtri(phi_a + u * (1.0 - phi_a)), dtype=np.float64)


def _zone_sd(zone: StorageZoneDoc, quantity: Literal["temperature_k", "relative_humidity"]) -> float:
    """Standard deviation of a zone's offset (spec 9.7): the sensor's calibration standard
    uncertainty for a sensed quantity, else the declared prior's sigma, else 0."""
    for sensor in zone["sensors"]:
        if sensor["quantity"] == quantity:
            return float(sensor["calibration_standard_uncertainty"]["value"])
    declared = zone["declared_conditions"]
    prior = (
        declared["temperature_k"] if quantity == "temperature_k" else declared["relative_humidity_percent"]
    )
    return 0.0 if prior is None else float(prior["sigma"])


def draw_parameters(
    model: StabilityModel,
    initial_assay_mean_percent: float,
    initial_assay_sd_percent: float,
    zones: Mapping[str, StorageZoneDoc],
    zone_ids: Sequence[str],
    generator: np.random.Generator,
    n: int,
) -> DrawBlock:
    """Draw one batch following the fixed variate layout of spec 9.7. All variates come
    from ``generator.standard_normal`` and ``generator.random`` in the documented order;
    truncated normals use the inverse-CDF transform of one uniform (scipy ``ndtri`` on the
    truncated probability range), so the layout never depends on parameter values."""
    if n <= 0:
        raise InputError(f"a batch needs at least one draw, got {n!r}")
    pathways = model.pathways
    ids = [pathway.pathway_id for pathway in pathways]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise InputError("model pathways must be sorted by unique pathway_id (spec 9.7)")
    zone_list = sorted(
        set(zone_ids) | {p.zone_correction.zone_id for p in pathways if p.zone_correction is not None}
    )
    for zone_id in zone_list:
        if zone_id not in zones:
            raise InputError(f"unknown zone {zone_id!r}")
    if not (math.isfinite(initial_assay_mean_percent) and math.isfinite(initial_assay_sd_percent)):
        raise InputError("the initial assay mean and sd must be finite")
    if initial_assay_sd_percent < 0.0:
        raise InputError("the initial assay sd must be non-negative")
    count = len(pathways)
    columns = _HEAD_COLUMNS + _SLOTS_PER_PATHWAY * count + _SLOTS_PER_ZONE * len(zone_list)
    z = generator.standard_normal((n, columns))
    u = generator.random((n, count))
    c0 = initial_assay_mean_percent + initial_assay_sd_percent * z[:, 0]
    if bool(np.any(c0 <= 0.0)):
        raise ComputationError("a drawn initial content C0 is not positive")
    z_cls = z[:, 1]
    arrays = {
        name: np.zeros((n, count), dtype=np.float64)
        for name in (
            "ln_k_ref_per_s",
            "ea_j_per_mol",
            "b_per_percent_rh",
            "ln_k_vis_per_lux_h",
            "ln_k_uv_per_w_h_per_m2",
            "ln_k_dose_per_gy",
            "ln_k_dose_frozen_per_gy",
            "ln_frozen_rate_multiplier",
        )
    }
    z_temperature = np.zeros((n, count), dtype=np.float64)
    z_humidity = np.zeros((n, count), dtype=np.float64)
    for p, pathway in enumerate(pathways):
        base = _HEAD_COLUMNS + _SLOTS_PER_PATHWAY * p
        z_temperature[:, p] = z[:, base + _SLOT_Z_TEMPERATURE]
        z_humidity[:, p] = z[:, base + _SLOT_Z_HUMIDITY]
        block = pathway.parameters
        names = block.names
        mean = np.array(block.mean, dtype=np.float64)
        covariance = np.array(block.covariance, dtype=np.float64)
        if mean.shape != (len(names),) or covariance.shape != (len(names), len(names)):
            raise ModelDomainError(f"pathway {pathway.pathway_id!r} has an inconsistent parameter block")
        if not (bool(np.all(np.isfinite(mean))) and bool(np.all(np.isfinite(covariance)))):
            raise ModelDomainError(f"pathway {pathway.pathway_id!r} has a non-finite parameter block")
        if bool(np.any(np.diag(covariance) < 0.0)):
            raise ModelDomainError(f"pathway {pathway.pathway_id!r} has a negative variance")
        ea_index = names.index("ea_j_per_mol") if "ea_j_per_mol" in names else None
        ea = np.zeros(n, dtype=np.float64)
        if ea_index is not None:
            ea = _truncated_ea(
                float(mean[ea_index]),
                math.sqrt(covariance[ea_index, ea_index]),
                block.ea_lower_j_per_mol,
                u[:, p],
            )
            arrays["ea_j_per_mol"][:, p] = ea
        others = [i for i in range(len(names)) if i != ea_index]
        if _is_measured_block(pathway):
            _draw_measured(arrays, p, names, mean, covariance, ea_index, ea, others, z, base)
        else:
            for i in others:
                name = names[i]
                sd = math.sqrt(covariance[i, i])
                slot = z[:, base + _SLOT[name]]
                if name == "ln_k_ref_per_s":
                    w = block.shared_weight
                    if not 0.0 <= w <= 1.0:
                        raise ModelDomainError(f"shared_weight must lie in [0, 1], got {w!r}")
                    correction = pathway.zone_correction
                    if correction is None:
                        arrays[name][:, p] = mean[i] + sd * (math.sqrt(w) * z_cls + math.sqrt(1.0 - w) * slot)
                    else:
                        weight = correction.weight
                        prior = zones[correction.zone_id]["rate_multiplier"]
                        factor_sd = float(prior["sigma"]) if prior["family"] == "lognormal" else 0.0
                        if not (math.isfinite(weight) and 0.0 < weight <= 1.0):
                            raise ModelDomainError("zone correction weight must lie in (0, 1]")
                        if correction.factor_sd_ln != factor_sd:
                            raise ModelDomainError("zone correction factor_sd_ln differs from the zone")
                        evidence_variance = covariance[i, i] / weight
                        if evidence_variance < factor_sd * factor_sd:
                            raise ModelDomainError(
                                "zone correction evidence variance is below factor variance"
                            )
                        zone_base = (
                            _HEAD_COLUMNS
                            + _SLOTS_PER_PATHWAY * count
                            + _SLOTS_PER_ZONE * zone_list.index(correction.zone_id)
                        )
                        arrays[name][:, p] = (
                            mean[i]
                            + sd * math.sqrt(w) * z_cls
                            + weight * math.sqrt(evidence_variance - factor_sd * factor_sd) * slot
                            - weight * factor_sd * z[:, zone_base]
                        )
                else:
                    arrays[name][:, p] = mean[i] + sd * slot
        if "ln_k_dose_frozen_per_gy" not in names:
            arrays["ln_k_dose_frozen_per_gy"][:, p] = arrays["ln_k_dose_per_gy"][:, p]
    _add_free_share_term(model, arrays["ln_k_ref_per_s"], z)
    multipliers: dict[str, FloatArray] = {}
    t_offsets: dict[str, FloatArray] = {}
    rh_offsets: dict[str, FloatArray] = {}
    for index, zone_id in enumerate(zone_list):
        base = _HEAD_COLUMNS + _SLOTS_PER_PATHWAY * count + _SLOTS_PER_ZONE * index
        zone = zones[zone_id]
        prior = zone["rate_multiplier"]
        multipliers[zone_id] = ln_rate_multiplier(prior["family"], prior["mu"], prior["sigma"], z[:, base])
        t_offsets[zone_id] = _zone_sd(zone, "temperature_k") * z[:, base + 1]
        rh_offsets[zone_id] = _zone_sd(zone, "relative_humidity") * z[:, base + 2]
    return DrawBlock(
        n=n,
        c0_percent=c0,
        z_c0=z[:, 0].copy(),
        ln_k_ref_per_s=arrays["ln_k_ref_per_s"],
        ea_j_per_mol=arrays["ea_j_per_mol"],
        b_per_percent_rh=arrays["b_per_percent_rh"],
        ln_k_vis_per_lux_h=arrays["ln_k_vis_per_lux_h"],
        ln_k_uv_per_w_h_per_m2=arrays["ln_k_uv_per_w_h_per_m2"],
        ln_k_dose_per_gy=arrays["ln_k_dose_per_gy"],
        ln_k_dose_frozen_per_gy=arrays["ln_k_dose_frozen_per_gy"],
        ln_frozen_multiplier=arrays["ln_frozen_rate_multiplier"],
        extrapolation_z_temperature=z_temperature,
        extrapolation_z_humidity=z_humidity,
        zone_ln_multiplier=multipliers,
        temperature_offset_k=t_offsets,
        rh_offset_percent=rh_offsets,
    )


def _log_softmax(x: FloatArray) -> FloatArray:
    """log_softmax over the last axis, x_i - ln sum_j exp(x_j), with the maximum
    subtracted first and the sum taken column by column in member order (spec 1.5)."""
    top = np.max(x, axis=-1)
    total = np.zeros(top.shape, dtype=np.float64)
    for col in range(x.shape[-1]):
        total = total + np.exp(x[..., col] - top)
    return np.asarray(x - (top + np.log(total))[..., np.newaxis], dtype=np.float64)


def free_group(model: StabilityModel) -> tuple[int, ...]:
    """Spec 9.5: indices (model order) of the free group F, the class-based pathways whose
    ``free_share`` is set. Anchored members (``free_share`` None) and measured blocks are
    never members."""
    return tuple(
        p
        for p, pathway in enumerate(model.pathways)
        if pathway.free_share is not None and not _is_measured_block(pathway)
    )


def _add_free_share_term(model: StabilityModel, ln_k: FloatArray, z: FloatArray) -> None:
    """Spec 9.5: each member of F (``free_group``) gains log_softmax_F(delta + s_m z_F)_i
    minus log_softmax_F(delta)_i on its drawn ln k*, with z_F the members' own ln k*
    columns (unused otherwise, because w = 1). A group of one never carries the term."""
    members = free_group(model)
    if len(members) < 2:
        return
    delta = np.zeros(len(members), dtype=np.float64)
    shifted = np.zeros((ln_k.shape[0], len(members)), dtype=np.float64)
    for col, p in enumerate(members):
        pathway = model.pathways[p]
        share = pathway.free_share
        assert share is not None
        if not (math.isfinite(share.delta_ln) and math.isfinite(share.method_sd_ln)):
            raise ModelDomainError(f"pathway {pathway.pathway_id!r} has a non-finite free_share")
        if share.method_sd_ln < 0.0:
            raise ModelDomainError(f"pathway {pathway.pathway_id!r} has a negative free_share method sd")
        if "ln_k_ref_per_s" not in pathway.parameters.names or pathway.parameters.shared_weight != 1.0:
            raise ModelDomainError(
                f"free group member {pathway.pathway_id!r} needs an ln k* block with shared_weight 1 "
                "(spec 9.5)"
            )
        delta[col] = share.delta_ln
        column = z[:, _HEAD_COLUMNS + _SLOTS_PER_PATHWAY * p + _SLOT["ln_k_ref_per_s"]]
        shifted[:, col] = share.delta_ln + share.method_sd_ln * column
    term = _log_softmax(shifted) - _log_softmax(delta)
    for col, p in enumerate(members):
        ln_k[:, p] = ln_k[:, p] + term[:, col]


def _draw_measured(
    arrays: Mapping[str, FloatArray],
    p: int,
    names: Sequence[ParameterName],
    mean: FloatArray,
    covariance: FloatArray,
    ea_index: int | None,
    ea: FloatArray,
    others: Sequence[int],
    z: FloatArray,
    base: int,
) -> None:
    """Conditional draw of a measured block's other parameters given Ea (spec 9.7)."""
    if not others:
        return
    idx = np.array(others, dtype=np.intp)
    conditional_mean = np.tile(mean[idx], (ea.size, 1))
    conditional_cov = covariance[np.ix_(idx, idx)]
    if ea_index is not None and covariance[ea_index, ea_index] > 0.0:
        c_ee = covariance[ea_index, ea_index]
        c_re = covariance[idx, ea_index]
        conditional_mean = conditional_mean + np.outer(ea - mean[ea_index], c_re / c_ee)
        conditional_cov = conditional_cov - np.outer(c_re, c_re) / c_ee
    factor = np.zeros_like(conditional_cov)
    positive = np.diag(conditional_cov) > 0.0
    if bool(np.any(positive)):
        sub = conditional_cov[np.ix_(positive, positive)]
        try:
            factor[np.ix_(positive, positive)] = np.linalg.cholesky(0.5 * (sub + sub.T))
        except np.linalg.LinAlgError as error:
            raise ComputationError(f"measured block covariance is not positive definite: {error}") from error
    slots = np.stack([z[:, base + _SLOT[names[i]]] for i in others], axis=1)
    # Explicit loops keep the sums in a fixed order (no BLAS reduction, spec 1.5).
    for row, i in enumerate(others):
        value = conditional_mean[:, row].copy()
        for col in range(len(others)):
            if factor[row, col] != 0.0:
                value = value + factor[row, col] * slots[:, col]
        arrays[names[i]][:, p] = value


@dataclasses.dataclass(frozen=True, slots=True)
class BatchOutcome:
    """What the adaptive loop needs from one evaluated batch: the reported measurand per
    draw (``values``), optional likelihood weights (assay update), and extra per-draw arrays
    the caller keeps (``extras``, for example degradant percent)."""

    values: FloatArray
    log_weights: FloatArray | None
    extras: Mapping[str, FloatArray]


@dataclasses.dataclass(frozen=True, slots=True)
class AdaptiveResult:
    """``batch_statistics`` (y, u(y), y_low, y_high per batch) is an addition so the
    caller can report 2 s / sqrt(h) of u(y) in the monte_carlo placard (spec 8.4;
    docs/requests/B1.md R-11)."""

    values: FloatArray
    log_weights: FloatArray | None
    extras: Mapping[str, FloatArray]
    n_batches: int
    batch_size: int
    converged: bool
    numerical_tolerance: float
    batch_statistics: tuple[tuple[float, float, float, float], ...] = ()


def _batch_statistics(
    values: FloatArray, log_weights: FloatArray | None, probability: float
) -> tuple[float, float, float, float]:
    weights = None if log_weights is None else normalized_weights(log_weights)
    w = np.full(values.size, 1.0 / values.size) if weights is None else weights
    mean = float(np.sum(w * values))
    deviation = values - mean
    u = math.sqrt(float(np.sum(w * deviation * deviation)))
    low = weighted_quantile(values, weights, (1.0 - probability) / 2.0)
    high = weighted_quantile(values, weights, (1.0 + probability) / 2.0)
    return mean, u, low, high


def run_adaptive(
    evaluate_batch: Callable[[int, np.random.Generator], BatchOutcome],
    seed: int,
    settings: MonteCarloSettings,
    *,
    ess_floor: float | None = None,
    stop_after_initial: Callable[[Mapping[str, FloatArray]], bool] | None = None,
) -> AdaptiveResult:
    """JCGM 101 7.9 adaptive procedure (spec 9.9). Batches r = 0, 1, ... of size
    ``jcgm_batch_size(p)`` drawn from ``batch_generator(seed, r)``. Each batch yields four
    statistics from its own draws (weights normalized within the batch when log weights are
    given): y (mean), u(y), y_low and y_high. After h >= min_batches batches, with s_v the
    sample standard deviation (divisor h - 1) of statistic v over the h batches, stop when
    2 s_v / sqrt(h) <= delta for all four, delta from ``numerical_tolerance`` of u(y) over
    all draws so far (globally normalized weights), and, when log weights are given and
    ``ess_floor`` is set, the effective sample size over all draws is >= ess_floor. Stop
    unconverged at max_batches (flag mc_not_converged). Draws are returned in batch order."""
    probability = float(settings["coverage_probability"]["value"])
    batch_size = jcgm_batch_size(probability)
    n_dig = int(settings["n_dig"]["value"])
    max_batches = int(settings["max_batches"]["value"])
    min_batches = int(settings["min_batches"]["value"])
    if (
        max_batches < 1
        or min_batches < 1
        or min_batches > max_batches
        or (min_batches == 1 and max_batches != 1)
    ):
        raise InputError("monte_carlo needs 2 <= min_batches <= max_batches, or both equal 1")
    values: list[FloatArray] = []
    log_weights: list[FloatArray] = []
    extras: dict[str, list[FloatArray]] = {}
    statistics: list[tuple[float, float, float, float]] = []
    weighted: bool | None = None
    converged = False
    delta = 0.0
    for r in range(max_batches):
        outcome = evaluate_batch(r, batch_generator(seed, r))
        batch_values = np.asarray(outcome.values, dtype=np.float64)
        if batch_values.shape != (batch_size,) or not bool(np.all(np.isfinite(batch_values))):
            raise ComputationError(f"batch {r} must give {batch_size} finite values")
        has_weights = outcome.log_weights is not None
        if weighted is None:
            weighted = has_weights
            extra_keys = sorted(outcome.extras)
            extras = {key: [] for key in extra_keys}
        elif weighted != has_weights or sorted(outcome.extras) != sorted(extras):
            raise ComputationError("every batch must return the same kind of outcome")
        batch_log = None
        if outcome.log_weights is not None:
            batch_log = np.asarray(outcome.log_weights, dtype=np.float64)
            if batch_log.shape != (batch_size,):
                raise ComputationError(f"batch {r} log weights must have shape ({batch_size},)")
            log_weights.append(batch_log)
        values.append(batch_values)
        for key in sorted(outcome.extras):
            extras[key].append(np.asarray(outcome.extras[key], dtype=np.float64))
        statistics.append(_batch_statistics(batch_values, batch_log, probability))
        all_values = np.concatenate(values)
        all_weights = normalized_weights(np.concatenate(log_weights)) if weighted else None
        w = np.full(all_values.size, 1.0 / all_values.size) if all_weights is None else all_weights
        mean = float(np.sum(w * all_values))
        deviation = all_values - mean
        delta = numerical_tolerance(math.sqrt(float(np.sum(w * deviation * deviation))), n_dig)
        h = len(statistics)
        if (
            h == min_batches
            and stop_after_initial is not None
            and stop_after_initial({key: np.concatenate(parts) for key, parts in extras.items()})
        ):
            break
        if min_batches == max_batches == 1:
            converged = True
            break
        if h >= min_batches:
            table = np.array(statistics, dtype=np.float64)
            spread = 2.0 * np.std(table, axis=0, ddof=1) / math.sqrt(h)
            stable = bool(np.all(spread <= delta))
            enough = True
            if weighted and ess_floor is not None and all_weights is not None:
                enough = effective_sample_size(all_weights) >= ess_floor
            if stable and enough:
                converged = True
                break
    return AdaptiveResult(
        values=np.concatenate(values),
        log_weights=np.concatenate(log_weights) if weighted else None,
        extras={key: np.concatenate(parts) for key, parts in extras.items()},
        n_batches=len(statistics),
        batch_size=batch_size,
        converged=converged,
        numerical_tolerance=delta,
        batch_statistics=tuple(statistics),
    )

"""Offline fitting of stability studies into measured models (stability-models.json).

Owner: B1. Contract: docs/ENGINE-SPEC.md section 14. Runs offline (never inside an
estimate). A fitted model enters configuration only through review (review_state).
"""

import dataclasses
import hashlib
import math
from collections.abc import Sequence
from typing import Final, cast, get_args

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from pharmacy import microclimate
from pharmacy.config import canonical_json_bytes, validate_instance
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, PERCENT
from pharmacy.errors import ComputationError, ConvergenceError, InputError
from pharmacy.kinetics import (
    _humidity_integral_array,
    conversion_from_theta_array,
    rate_function,
    theta_from_conversion,
)
from pharmacy.types import (
    JSONValue,
    NumericsSettings,
    PackageDoc,
    ParameterName,
    RateLawKind,
    Segment,
    StabilityModelDoc,
)

type _Array = NDArray[np.float64]

_LEAST_SQUARES_TOLERANCE: Final[float] = 1e-12
"""ftol = xtol = gtol of the whole-study fit, fixed by spec 14.3."""

_SELECTION_DELTA_AICC: Final[float] = 2.0
"""Models within this AICc of the best are treated as tied (spec 14.4)."""

_KIND_ORDER: Final[tuple[RateLawKind, ...]] = get_args(RateLawKind)
_PARAMETER_NAMES: Final[tuple[ParameterName, ...]] = get_args(ParameterName)
_LOG_FLOAT_MAX: Final[float] = math.log(float(np.finfo(np.float64).max))
_STABILITY_MODELS_SCHEMA: Final[str] = "pharmacy/stability-models.schema.json"

_LN_K: Final[str] = "ln_k_ref_per_s"
_EA: Final[str] = "ea_j_per_mol"
_B: Final[str] = "b_per_percent_rh"
_C0: Final[str] = "c0_percent"


def _finite_array(name: str, values: Sequence[float]) -> _Array:
    array = np.asarray([float(v) for v in values], dtype=np.float64)
    if not bool(np.all(np.isfinite(array))):
        raise InputError(f"{name} must be finite")
    return array


def _inverse_temperature_coordinate(
    temperatures_k: _Array, reference_temperature_k: float, gas_constant_j_per_mol_k: float
) -> _Array:
    """x = -(1/T - 1/T_ref)/R, with 1/T - 1/T_ref written as (T_ref - T)/(T T_ref) so a
    temperature close to the reference does not cancel."""
    return (temperatures_k - reference_temperature_k) / (
        temperatures_k * reference_temperature_k * gas_constant_j_per_mol_k
    )


def _check_reference(reference_temperature_k: float, gas_constant_j_per_mol_k: float) -> None:
    if not math.isfinite(reference_temperature_k) or reference_temperature_k <= 0.0:
        raise InputError(
            f"reference temperature must be finite and positive, got {reference_temperature_k!r}"
        )
    if not math.isfinite(gas_constant_j_per_mol_k) or gas_constant_j_per_mol_k <= 0.0:
        raise InputError(f"gas constant must be finite and positive, got {gas_constant_j_per_mol_k!r}")


# =========================================================================================
# Two-stage Arrhenius (spec 14.1)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class ArrheniusFit:
    """Two-stage Arrhenius fit: OLS of ln k on x = -(1/T - 1/T_ref)/R. ``ln_k_ref`` is in
    ln of the input rate unit at ``reference_temperature_k``; ``ln_a`` is the intercept at
    1/T = 0 in the same unit. Covariance is the OLS covariance of (ln_k_ref, ea) with
    s^2 = RSS/(n - 2)."""

    ea_j_per_mol: float
    ea_se_j_per_mol: float
    ln_k_ref: float
    ln_k_ref_se: float
    covariance: tuple[tuple[float, float], tuple[float, float]]
    ln_a: float
    reference_temperature_k: float
    r2: float
    rss: float
    dof: int


def fit_arrhenius_two_stage(
    temperatures_k: Sequence[float],
    rate_constants: Sequence[float],
    reference_temperature_k: float,
    gas_constant_j_per_mol_k: float = GAS_CONSTANT_J_PER_MOL_K,
) -> ArrheniusFit:
    """Stage two of the two-stage method (spec 14.1). Needs n >= 3 distinct temperatures
    and positive rate constants (``InputError`` otherwise). Vectors TV-KIN-1 to TV-KIN-3."""
    if len(temperatures_k) != len(rate_constants):
        raise InputError(
            f"mismatched lengths: {len(temperatures_k)} temperatures and {len(rate_constants)} rate constants"
        )
    _check_reference(reference_temperature_k, gas_constant_j_per_mol_k)
    temperatures = _finite_array("temperatures", temperatures_k)
    rates = _finite_array("rate constants", rate_constants)
    if bool(np.any(temperatures <= 0.0)):
        raise InputError("temperatures must be positive")
    if bool(np.any(rates <= 0.0)):
        raise InputError("rate constants must be positive")
    n = temperatures.size
    if n < 3 or np.unique(temperatures).size < 3:
        raise InputError(f"the two-stage fit needs at least 3 distinct temperatures, got {n} points")
    x = _inverse_temperature_coordinate(temperatures, reference_temperature_k, gas_constant_j_per_mol_k)
    y = np.log(rates)
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    dx = x - x_mean
    dy = y - y_mean
    sxx = float(np.dot(dx, dx))
    ea = float(np.dot(dx, dy)) / sxx
    intercept = y_mean - ea * x_mean
    residuals = y - (intercept + ea * x)
    rss = float(np.dot(residuals, residuals))
    tss = float(np.dot(dy, dy))
    dof = n - 2
    s2 = rss / dof
    var_ea = s2 / sxx
    var_intercept = s2 * (1.0 / n + x_mean * x_mean / sxx)
    cov = -s2 * x_mean / sxx
    # All ln k equal: TSS = RSS = 0, a perfect (flat) fit; r^2 is reported as 1 instead of
    # 0/0 (docs/requests/B1.md R-FIT-6).
    r2 = 1.0 - rss / tss if tss > 0.0 else 1.0
    ln_a = intercept + ea / (gas_constant_j_per_mol_k * reference_temperature_k)
    return ArrheniusFit(
        ea_j_per_mol=ea,
        ea_se_j_per_mol=math.sqrt(var_ea),
        ln_k_ref=intercept,
        ln_k_ref_se=math.sqrt(var_intercept),
        covariance=((var_intercept, cov), (cov, var_ea)),
        ln_a=ln_a,
        reference_temperature_k=float(reference_temperature_k),
        r2=r2,
        rss=rss,
        dof=dof,
    )


# =========================================================================================
# Log-linear first order (spec 14.2)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class FirstOrderFit:
    """Log-linear first-order fit ln(C) = ln(C0) - k x. ``k`` is per unit of x (time or
    dose). With a fixed C0 the intercept is not estimated and ``c0_se`` is None."""

    k: float
    k_se: float
    c0_percent: float
    c0_se_percent: float | None
    rss_log: float
    dof: int


def fit_first_order_loglinear(
    x: Sequence[float],
    percent: Sequence[float],
    *,
    fixed_c0_percent: float | None = None,
) -> FirstOrderFit:
    """OLS of ln(percent) on x (spec 14.2). Vectors TV-KIN-6 (free intercept and C0 = 100)
    and TV-RAD-1 (intercept fixed at the initial concentration)."""
    if len(x) != len(percent):
        raise InputError(f"mismatched lengths: {len(x)} abscissae and {len(percent)} responses")
    xs = _finite_array("x", x)
    ys = _finite_array("percent", percent)
    if bool(np.any(ys <= 0.0)):
        raise InputError("responses must be positive for a log-linear fit")
    log_c = np.log(ys)
    n = xs.size
    if fixed_c0_percent is not None:
        if not math.isfinite(fixed_c0_percent) or fixed_c0_percent <= 0.0:
            raise InputError(f"fixed C0 must be finite and positive, got {fixed_c0_percent!r}")
        sum_x2 = float(np.dot(xs, xs))
        if n < 2 or sum_x2 == 0.0:
            raise InputError("a fixed-intercept fit needs at least 2 points and a nonzero abscissa")
        shifted = log_c - math.log(fixed_c0_percent)
        k = -float(np.dot(xs, shifted)) / sum_x2
        residuals = shifted + k * xs
        rss = float(np.dot(residuals, residuals))
        dof = n - 1
        s2 = rss / dof
        return FirstOrderFit(
            k=k,
            k_se=math.sqrt(s2 / sum_x2),
            c0_percent=float(fixed_c0_percent),
            c0_se_percent=None,
            rss_log=rss,
            dof=dof,
        )
    if n < 3 or np.unique(xs).size < 2:
        raise InputError("a free-intercept fit needs at least 3 points at 2 or more distinct abscissae")
    x_mean = float(np.mean(xs))
    y_mean = float(np.mean(log_c))
    dx = xs - x_mean
    sxx = float(np.dot(dx, dx))
    slope = float(np.dot(dx, log_c - y_mean)) / sxx
    intercept = y_mean - slope * x_mean
    residuals = log_c - (intercept + slope * xs)
    rss = float(np.dot(residuals, residuals))
    dof = n - 2
    s2 = rss / dof
    c0 = math.exp(intercept)
    # se(C0) by the delta method on C0 = exp(intercept) (docs/requests/B1.md R-FIT-7).
    se_intercept = math.sqrt(s2 * (1.0 / n + x_mean * x_mean / sxx))
    return FirstOrderFit(
        k=-slope,
        k_se=math.sqrt(s2 / sxx),
        c0_percent=c0,
        c0_se_percent=c0 * se_intercept,
        rss_log=rss,
        dof=dof,
    )


# =========================================================================================
# Whole-study weighted nonlinear least squares (spec 14.3)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class StudyObservation:
    """One assay of a stability study."""

    time_s: float
    temperature_k: float
    rh_percent: float | None
    response_percent: float
    sd_percent: float | None
    group: str
    chamber_log: tuple[Segment, ...] = ()
    package: PackageDoc | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class StudyFit:
    """Weighted nonlinear least-squares fit of a whole study (spec 14.3). ``names`` orders
    ``mean`` and ``covariance`` (ln_k_ref_per_s, ea_j_per_mol, [b_per_percent_rh],
    [c0_percent]); covariance = s^2 (J^T W J)^-1 with s^2 = RSS_w / dof (or 1 when the
    weights are absolute).

    The members after ``converged`` describe the study and the kind parameters so that
    ``model_document`` can write a complete entry (docs/requests/B1.md R-FIT-1); they are
    filled by ``fit_stability_study`` and default to None for hand-built values."""

    rate_law_kind: RateLawKind
    names: tuple[str, ...]
    mean: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    rss_weighted: float
    dof: int
    aic: float | None
    aicc: float | None
    bic: float | None
    reference_temperature_k: float
    reference_rh_percent: float
    converged: bool
    # docs/requests/B1.md R-FIT-1: study description needed by model_document (spec 14.5).
    n_observations: int | None = None
    temperature_min_k: float | None = None
    temperature_max_k: float | None = None
    rh_min_percent: float | None = None
    rh_max_percent: float | None = None
    max_time_s: float | None = None
    order_n: float | None = None
    alpha0: float | None = None
    avrami_n: float | None = None
    initial_percent: float | None = None
    observations: tuple[StudyObservation, ...] = ()
    inputs_sha256: str | None = None
    numerics: NumericsSettings | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _Study:
    """Validated observations as arrays."""

    time_s: _Array
    temperature_k: _Array
    rh_percent: _Array | None
    response: _Array
    sd: _Array
    groups: tuple[str, ...]


def _study_arrays(
    observations: Sequence[StudyObservation], *, fit_humidity: bool, absolute_weights: bool
) -> _Study:
    if not observations:
        raise InputError("a stability study needs observations")
    times = _finite_array("observation times", [o.time_s for o in observations])
    temperatures = _finite_array("observation temperatures", [o.temperature_k for o in observations])
    responses = _finite_array("responses", [o.response_percent for o in observations])
    if bool(np.any(times < 0.0)):
        raise InputError("observation times must be non-negative")
    if bool(np.any(temperatures <= 0.0)):
        raise InputError("observation temperatures must be positive")
    rh_values = [o.rh_percent for o in observations]
    rh: _Array | None = None
    if all(value is not None for value in rh_values):
        rh = _finite_array("observation RH", [float(v) for v in rh_values if v is not None])
        if bool(np.any((rh < 0.0) | (rh > PERCENT))):
            raise InputError("observation RH must lie in [0, 100] %RH")
    elif fit_humidity:
        raise InputError("fitting the humidity term needs the RH of every observation")
    sds = [o.sd_percent for o in observations]
    given = [value is not None for value in sds]
    if any(given) and not all(given):
        raise InputError("either every observation has sd_percent or none has")
    if all(given):
        sd = _finite_array("observation sd", [float(v) for v in sds if v is not None])
        if bool(np.any(sd <= 0.0)):
            raise InputError("observation sd_percent must be positive")
    else:
        if absolute_weights:
            raise InputError("absolute weights need sd_percent on every observation")
        sd = np.ones_like(responses)
    return _Study(
        time_s=times,
        temperature_k=temperatures,
        rh_percent=rh,
        response=responses,
        sd=sd,
        groups=tuple(o.group for o in observations),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _StudyHistory:
    durations: _Array
    temperatures: _Array
    outside: _Array
    inside: _Array
    tau_m: _Array
    observation_index: NDArray[np.intp]
    rh_bounds: tuple[float, float]


def _study_history(observations: Sequence[StudyObservation]) -> _StudyHistory:
    durations: list[float] = []
    temperatures: list[float] = []
    outside: list[float] = []
    inside: list[float] = []
    constants: list[float] = []
    indices: list[int] = []
    bounds: list[float] = []
    for index, observation in enumerate(observations):
        package = observation.package
        package_id = None if package is None else package["package_id"]
        rh = observation.rh_percent
        if rh is None:
            if package is not None or observation.chamber_log:
                raise InputError("logged or packaged observations require RH")
            rh = 0.0  # unused when the humidity term is absent
        segments = observation.chamber_log or (
            Segment(
                0.0,
                observation.time_s,
                observation.group,
                package_id,
                observation.temperature_k,
                rh,
                rh,
                0.0,
                0.0,
                0.0,
                (),
                (),
            ),
        )
        previous = 0.0
        for segment in segments:
            if (
                not all(
                    math.isfinite(v)
                    for v in (
                        segment.t0_s,
                        segment.t1_s,
                        segment.temperature_k,
                        segment.relative_humidity_percent,
                    )
                )
                or segment.t0_s != previous
                or segment.t1_s < segment.t0_s
                or segment.temperature_k <= 0.0
                or not 0.0 <= segment.relative_humidity_percent <= PERCENT
            ):
                raise InputError(
                    "chamber logs need contiguous finite intervals from zero with valid T and RH"
                )
            previous = segment.t1_s
        if previous != observation.time_s:
            raise InputError("chamber log must end at its observation time")
        segments = tuple(dataclasses.replace(segment, package_id=package_id) for segment in segments)
        if package is None:
            starts = ends = tuple(segment.relative_humidity_percent for segment in segments)
            taus = (0.0,) * len(segments)
        else:
            trace = microclimate.internal_rh_trace(segments, {package["package_id"]: package})
            starts, ends, taus = trace.rh_in_start_high, trace.rh_in_end_high, trace.time_constant_s
        for segment, start, end, tau in zip(segments, starts, ends, taus, strict=True):
            durations.append(segment.t1_s - segment.t0_s)
            temperatures.append(segment.temperature_k)
            outside.append(segment.relative_humidity_percent)
            inside.append(start)
            constants.append(tau)
            indices.append(index)
            bounds.extend((start, end))
    return _StudyHistory(
        np.asarray(durations),
        np.asarray(temperatures),
        np.asarray(outside),
        np.asarray(inside),
        np.asarray(constants),
        np.asarray(indices, dtype=np.intp),
        (min(bounds), max(bounds)),
    )


def _history_theta(
    history: _StudyHistory,
    ln_k: float,
    ea: float,
    b: float,
    reference_temperature: float,
    reference_rh: float,
    n: int,
    numerics: NumericsSettings | None,
) -> _Array:
    coordinate = _inverse_temperature_coordinate(
        history.temperatures, reference_temperature, GAS_CONSTANT_J_PER_MOL_K
    )
    kwargs = (
        {}
        if numerics is None
        else {
            "rel_tol": float(numerics["series_relative_tolerance"]["value"]),
            "max_terms": int(numerics["series_max_terms"]["value"]),
        }
    )
    # Both this fitter and the estimate call the same vectorized humidity integral.
    rel_tol = float(kwargs.get("rel_tol", 1e-15))
    max_terms = int(kwargs.get("max_terms", 500))
    integral = _humidity_integral_array(
        b * (history.inside - history.outside), history.durations, history.tau_m, rel_tol, max_terms
    )
    if not bool(np.all(np.isfinite(integral))):
        raise ComputationError("study humidity integral is nonfinite")
    positive = integral > 0.0
    terms = np.zeros_like(integral)
    exponent = ln_k + ea * coordinate + b * (history.outside - reference_rh)
    terms[positive] = np.exp(
        np.minimum(
            exponent[positive] + np.log(integral[positive]), _LOG_FLOAT_MAX - math.log(max(1, integral.size))
        )
    )
    if not bool(np.all(np.isfinite(terms))):
        raise ComputationError("study history produced a nonfinite integrated rate")
    return np.asarray(np.bincount(history.observation_index, weights=terms, minlength=n), dtype=np.float64)


def _fixed_initial(study: _Study, initial_percent: float | None) -> float:
    """C0 when it is not fitted: the caller's value, else the mean of the time-zero
    observations, else 100 % of label claim (docs/requests/B1.md R-FIT-3)."""
    if initial_percent is not None:
        if not math.isfinite(initial_percent) or initial_percent <= 0.0:
            raise InputError(f"initial_percent must be finite and positive, got {initial_percent!r}")
        return float(initial_percent)
    at_zero = study.response[study.time_s == 0.0]
    if at_zero.size:
        return float(np.mean(at_zero))
    return PERCENT


def _start_initial(study: _Study) -> float:
    at_zero = study.response[study.time_s == 0.0]
    if at_zero.size:
        return float(np.mean(at_zero))
    return float(np.max(study.response))


def _starting_values(
    study: _Study,
    kind: RateLawKind,
    c0: float,
    x_t: _Array,
    d_rh: _Array | None,
    order_n: float | None,
    alpha0: float | None,
    avrami_n: float | None,
) -> tuple[float, float, float]:
    """Two-stage starts (ln k*, Ea, B): per group, Theta of each pull from the kind's g
    applied to the observed conversion, k_g = sum t Theta / sum t^2 (through the origin);
    then OLS of ln k_g on (1, x, RH - RH*) over the groups with k_g > 0, on unit-norm
    columns and with the minimum-norm solution when the groups do not identify every
    coefficient. No usable group: ln k* = -ln(longest time), Ea = B = 0 (one unit of Theta
    over the study; docs/requests/B1.md R-FIT-4)."""
    rows: list[tuple[float, float, float]] = []
    for group in sorted(set(study.groups)):
        mask = np.array([g == group for g in study.groups], dtype=np.bool_) & (study.time_s > 0.0)
        numerator = 0.0
        denominator = 0.0
        for t, response in zip(study.time_s[mask], study.response[mask], strict=True):
            alpha = 1.0 - float(response) / c0
            if not 0.0 < alpha < 1.0:
                continue
            theta = theta_from_conversion(kind, alpha, order_n=order_n, alpha0=alpha0, avrami_n=avrami_n)
            numerator += float(t) * theta
            denominator += float(t) * float(t)
        if denominator > 0.0 and numerator > 0.0:
            idx = np.flatnonzero(mask)
            rows.append(
                (
                    math.log(numerator / denominator),
                    float(np.mean(x_t[idx])),
                    float(np.mean(d_rh[idx])) if d_rh is not None else 0.0,
                )
            )
    if not rows:
        return -math.log(float(np.max(study.time_s))), 0.0, 0.0
    y = np.array([row[0] for row in rows], dtype=np.float64)
    columns = [np.ones(len(rows)), np.array([row[1] for row in rows])]
    if d_rh is not None:
        columns.append(np.array([row[2] for row in rows]))
    design = np.column_stack(columns)
    norms = np.linalg.norm(design, axis=0)
    norms[norms == 0.0] = 1.0
    scaled, *_ = np.linalg.lstsq(design / norms, y, rcond=None)
    coefficients = scaled / norms
    b_start = float(coefficients[2]) if d_rh is not None else 0.0
    return float(coefficients[0]), float(coefficients[1]), b_start


def fit_stability_study(
    observations: Sequence[StudyObservation],
    kind: RateLawKind,
    reference_temperature_k: float,
    reference_rh_percent: float,
    *,
    fit_humidity: bool,
    fit_initial: bool,
    absolute_weights: bool,
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
    numerics: NumericsSettings | None = None,
    initial_percent: float | None = None,
) -> StudyFit:
    """WNLLS with scipy ``least_squares`` (method "trf", x_scale "jac", ftol = xtol = gtol =
    1e-12) on C = C0 (1 - G(k(T, RH) t)) (spec 14.3). Start values from the two-stage fit.
    Vectors TV-KIN-4, TV-H-5, TV-FIT-1.

    ``alpha0``, ``avrami_n`` (kinds B1 and An), ``numerics`` (the D2 and D4 inverses) and
    ``initial_percent`` (C0 when ``fit_initial`` is false) are keyword additions to the
    contract (docs/requests/B1.md R-FIT-2, R-FIT-3). Raises ``InputError`` for unusable
    observations or n <= p, ``ModelDomainError`` for invalid kind parameters,
    ``ComputationError`` for a singular information matrix
    and ``ConvergenceError`` when the optimizer fails outright. ``converged`` is false when
    the optimizer stopped at its evaluation limit."""
    _check_reference(reference_temperature_k, GAS_CONSTANT_J_PER_MOL_K)
    if not math.isfinite(reference_rh_percent):
        raise InputError(f"reference RH must be finite, got {reference_rh_percent!r}")
    # Validates the kind and its parameters (ModelDomainError) before any data work.
    conversion_from_theta_array(
        kind, np.zeros(1), order_n=order_n, alpha0=alpha0, avrami_n=avrami_n, numerics=numerics
    )
    study = _study_arrays(observations, fit_humidity=fit_humidity, absolute_weights=absolute_weights)
    names: list[str] = [_LN_K, _EA]
    if fit_humidity:
        names.append(_B)
    if fit_initial:
        names.append(_C0)
    p = len(names)
    n = study.response.size
    if n <= p:
        raise InputError(f"{n} observations cannot fit {p} parameters (needs n > p)")
    history = _study_history(observations)
    logged = any(o.chamber_log or o.package is not None for o in observations)
    if np.unique(history.temperatures).size < 2:
        raise InputError("the activation energy needs observations at 2 or more temperatures")
    d_rh: _Array | None = None
    if fit_humidity and study.rh_percent is not None:  # _study_arrays requires RH when fitting it
        if history.rh_bounds[0] == history.rh_bounds[1]:
            raise InputError("the humidity term needs observations at 2 or more RH values")
        d_rh = study.rh_percent - reference_rh_percent
    if np.max(study.time_s) <= 0.0:
        raise InputError("a stability study needs at least one pull after time zero")
    x_t = _inverse_temperature_coordinate(
        study.temperature_k, reference_temperature_k, GAS_CONSTANT_J_PER_MOL_K
    )
    fixed_c0 = PERCENT if fit_initial else _fixed_initial(study, initial_percent)
    c0_start = _start_initial(study) if fit_initial else fixed_c0
    ln_k0, ea0, b0 = _starting_values(study, kind, c0_start, x_t, d_rh, order_n, alpha0, avrami_n)
    start = [ln_k0, ea0]
    if fit_humidity:
        start.append(b0)
    if fit_initial:
        start.append(c0_start)
    positive_time = study.time_s > 0.0
    log_time = np.zeros_like(study.time_s)
    log_time[positive_time] = np.log(study.time_s[positive_time])

    def unpack(params: _Array) -> tuple[_Array, float]:
        ln_k = params[0] + params[1] * x_t
        if d_rh is not None:
            ln_k = ln_k + params[2] * d_rh
        c0 = float(params[-1]) if fit_initial else fixed_c0
        theta = np.zeros_like(study.time_s)
        # Theta = exp(ln k + ln t), capped at the largest float so a trial step far out of
        # range saturates G instead of overflowing.
        theta[positive_time] = np.exp(
            np.minimum(ln_k[positive_time] + log_time[positive_time], _LOG_FLOAT_MAX)
        )
        if logged:
            theta = _history_theta(
                history,
                float(params[0]),
                float(params[1]),
                float(params[2]) if fit_humidity else 0.0,
                reference_temperature_k,
                reference_rh_percent,
                n,
                numerics,
            )
        return theta, c0

    def conversion(theta: _Array) -> _Array:
        return conversion_from_theta_array(
            kind, theta, order_n=order_n, alpha0=alpha0, avrami_n=avrami_n, numerics=numerics
        )

    def residuals(params: _Array) -> _Array:
        theta, c0 = unpack(params)
        predicted = c0 * (1.0 - conversion(theta))
        out = (study.response - predicted) / study.sd
        if not bool(np.all(np.isfinite(out))):
            raise ComputationError("the study model produced a non-finite residual")
        return np.asarray(out, dtype=np.float64)

    def jacobian(params: _Array) -> _Array:
        theta, c0 = unpack(params)
        alpha = conversion(theta)
        # d alpha / d ln k = Theta f(alpha) (g' = 1/f); 0 at Theta = 0 and beyond completion.
        sensitivity = np.zeros_like(theta)
        for i in range(theta.size):
            a = float(alpha[i])
            if theta[i] > 0.0 and 0.0 < a < 1.0:
                sensitivity[i] = float(theta[i]) * rate_function(
                    kind, a, order_n=order_n, alpha0=alpha0, avrami_n=avrami_n
                )
        d_pred_d_lnk = -c0 * sensitivity
        columns = [d_pred_d_lnk, d_pred_d_lnk * x_t]
        if d_rh is not None:
            columns.append(d_pred_d_lnk * d_rh)
        if fit_initial:
            columns.append(1.0 - alpha)
        return np.asarray(-np.column_stack(columns) / study.sd[:, None], dtype=np.float64)

    result = least_squares(
        residuals,
        np.asarray(start, dtype=np.float64),
        jac="3-point" if logged else jacobian,
        method="trf",
        x_scale="jac",
        ftol=_LEAST_SQUARES_TOLERANCE,
        xtol=_LEAST_SQUARES_TOLERANCE,
        gtol=_LEAST_SQUARES_TOLERANCE,
    )
    if int(result.status) < 0 or not bool(np.all(np.isfinite(result.x))):
        raise ConvergenceError(f"the study fit failed: {result.message}")
    estimate = np.asarray(result.x, dtype=np.float64)
    final_residuals = residuals(estimate)
    rss_w = float(np.dot(final_residuals, final_residuals))
    dof = n - p
    s2 = 1.0 if absolute_weights else rss_w / dof
    covariance = _covariance(np.asarray(result.jac) if logged else jacobian(estimate), s2)
    log_term = n * math.log(rss_w / n) if rss_w > 0.0 else None
    aic = None if log_term is None else 2.0 * p + log_term
    aicc = None if aic is None or n <= p + 1 else aic + 2.0 * p * (p + 1) / (n - p - 1)
    bic = None if log_term is None else p * math.log(n) + log_term
    material = cast(
        JSONValue,
        {
            "observations": [dataclasses.asdict(o) for o in observations],
            "kind": kind,
            "reference_temperature_k": reference_temperature_k,
            "reference_rh_percent": reference_rh_percent,
            "fit_humidity": fit_humidity,
            "fit_initial": fit_initial,
            "absolute_weights": absolute_weights,
            "order_n": order_n,
            "alpha0": alpha0,
            "avrami_n": avrami_n,
            "numerics": numerics,
            "initial_percent": initial_percent,
        },
    )
    digest = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    return StudyFit(
        rate_law_kind=kind,
        names=tuple(names),
        mean=tuple(float(v) for v in estimate),
        covariance=tuple(tuple(float(v) for v in row) for row in covariance),
        rss_weighted=rss_w,
        dof=dof,
        aic=aic,
        aicc=aicc,
        bic=bic,
        reference_temperature_k=float(reference_temperature_k),
        reference_rh_percent=float(reference_rh_percent),
        converged=int(result.status) > 0,
        n_observations=n,
        temperature_min_k=float(np.min(history.temperatures)),
        temperature_max_k=float(np.max(history.temperatures)),
        rh_min_percent=history.rh_bounds[0] if fit_humidity else None,
        rh_max_percent=history.rh_bounds[1] if fit_humidity else None,
        max_time_s=float(np.max(study.time_s)),
        order_n=order_n,
        alpha0=alpha0,
        avrami_n=avrami_n,
        initial_percent=None if fit_initial else fixed_c0,
        observations=tuple(observations),
        inputs_sha256=digest,
        numerics=numerics,
    )


def _covariance(jacobian: _Array, s2: float) -> _Array:
    """s^2 (J^T W J)^-1 from the weighted residual Jacobian, inverted on unit-norm columns
    so parameters of very different scale (ln k and Ea) do not hide a rank deficiency."""
    norms = np.linalg.norm(jacobian, axis=0)
    if bool(np.any(norms == 0.0)):
        raise ComputationError("a fitted parameter does not affect any observation")
    scaled = jacobian / norms
    if int(np.linalg.matrix_rank(scaled)) < scaled.shape[1]:
        raise ComputationError("the study does not identify every fitted parameter (singular J^T W J)")
    inverse = np.linalg.inv(scaled.T @ scaled)
    covariance = s2 * inverse / np.outer(norms, norms)
    if not bool(np.all(np.isfinite(covariance))) or bool(np.any(np.diag(covariance) < 0.0)):
        raise ComputationError("the study covariance is not finite and positive")
    return np.asarray(covariance, dtype=np.float64)


# =========================================================================================
# Model selection and the model document (spec 14.4, 14.5)
# =========================================================================================


def _predict(
    fit: StudyFit, observation: StudyObservation, mean: Sequence[float], history: _StudyHistory | None = None
) -> float:
    params = dict(zip(fit.names, mean, strict=True))
    if _LN_K not in params or _EA not in params:
        raise InputError("prediction requires ln_k_ref_per_s and ea_j_per_mol")
    if history is None:
        history = _study_history((observation,))
    theta = _history_theta(
        history,
        params[_LN_K],
        params[_EA],
        params.get(_B, 0.0),
        fit.reference_temperature_k,
        fit.reference_rh_percent,
        1,
        fit.numerics,
    )
    c0 = params.get(_C0, fit.initial_percent)
    if c0 is None or not math.isfinite(c0) or c0 <= 0.0:
        raise InputError("prediction requires a finite positive initial potency")
    alpha = conversion_from_theta_array(
        fit.rate_law_kind,
        theta,
        order_n=fit.order_n,
        alpha0=fit.alpha0,
        avrami_n=fit.avrami_n,
        numerics=fit.numerics,
    )
    return float(c0 * (1.0 - alpha[0]))


def prediction_interval(
    fit: StudyFit, observation: StudyObservation, *, assay_sd_percent: float = 0.0
) -> tuple[float, float]:
    """95% potency interval from 4000 normal fit draws, seeded from the fit inputs (14.4).

    The observation supplies prediction time, chamber history and package; its response
    is ignored. Set assay_sd_percent only for comparison with a measured assay (15.5).
    """
    if fit.inputs_sha256 is None:
        raise InputError("prediction intervals require the fit input hash")
    if not math.isfinite(assay_sd_percent) or assay_sd_percent < 0.0:
        raise InputError("assay sd must be finite and non-negative")
    mean = np.asarray(fit.mean)
    covariance = np.asarray(fit.covariance)
    if (
        mean.shape != (len(fit.names),)
        or covariance.shape != (mean.size, mean.size)
        or not bool(np.all(np.isfinite(mean)))
        or not bool(np.all(np.isfinite(covariance)))
        or not np.allclose(covariance, covariance.T)
    ):
        raise InputError("fit mean and covariance must be finite with matching dimensions")
    try:
        seed = int(fit.inputs_sha256, 16)
        generator = np.random.Generator(np.random.PCG64(seed))
        samples = generator.multivariate_normal(mean, covariance, size=4000, check_valid="raise")
    except ValueError as error:
        raise InputError("fit hash or covariance is invalid") from error
    history = _study_history((observation,))
    values = np.array([_predict(fit, observation, sample, history) for sample in samples])
    if assay_sd_percent:
        values += assay_sd_percent * generator.standard_normal(values.size)
    if not bool(np.all(np.isfinite(values))):
        raise ComputationError("prediction draws are nonfinite")
    low, high = np.quantile(values, (0.025, 0.975))
    if not (math.isfinite(float(low)) and math.isfinite(float(high))):
        raise ComputationError("prediction quantiles are nonfinite")
    return float(low), float(high)


def select_model(fits: Sequence[StudyFit], *, condition: StudyObservation | None = None) -> StudyFit:
    """Conservative potency among candidates within 2 AICc, then parameter count and kind.

    An explicit comparison condition overrides retained long-term observations. Otherwise
    the longest study pull identifies the long-term group, with its hottest pull as tie break.
    """
    if not fits:
        raise InputError("select_model needs at least one fit")
    if any(fit.aicc is None or not math.isfinite(fit.aicc) for fit in fits):
        raise InputError("select_model requires an AICc for every candidate")
    best = min(cast(float, fit.aicc) for fit in fits)
    tied = [fit for fit in fits if cast(float, fit.aicc) - best <= _SELECTION_DELTA_AICC]
    if len(tied) == 1:
        return tied[0]
    if condition is None:
        observations = tied[0].observations
        if not observations:
            raise InputError("model selection requires a retained or explicit study condition")
        condition = max(observations, key=lambda o: (o.time_s, o.temperature_k, o.group))
    comparison = condition
    return min(
        tied,
        key=lambda fit: (
            _predict(fit, comparison, fit.mean),
            len(fit.names),
            _KIND_ORDER.index(fit.rate_law_kind),
        ),
    )


def _parameter_name(name: str) -> ParameterName:
    for candidate in _PARAMETER_NAMES:
        if candidate == name:
            return candidate
    raise InputError(f"{name!r} is not a stability-model parameter name")


def model_document(
    fit: StudyFit,
    model_id: str,
    product_id: str,
    ingredient_id: str,
    package_id: str | None,
    dataset_sha256: str,
    source_ids: Sequence[str],
) -> StabilityModelDoc:
    """A stability-models.json entry (scope ``product``) from a reviewed fit; the
    validated range is the study's temperature and RH range and ``max_study_duration_s``
    its longest time point (spec 14.5).

    C0 is not a stability-model parameter, so a fitted ``c0_percent`` is dropped from the
    parameter vector (its marginal is not kept). The entry has no ``review_state`` member;
    the document that receives it carries review_state draft (docs/requests/B1.md R-FIT-5).
    Raises ``InputError`` when the fit lacks its study description, the hash is not 64
    lower-case hex digits or no source is given, and ``ConfigSchemaError`` if the entry does
    not validate."""
    if (
        fit.temperature_min_k is None
        or fit.temperature_max_k is None
        or fit.max_time_s is None
        or fit.n_observations is None
    ):
        raise InputError("the fit carries no study description; produce it with fit_stability_study")
    if len(dataset_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in dataset_sha256):
        raise InputError("dataset_sha256 must be 64 lower-case hexadecimal digits")
    sources = sorted(set(source_ids))
    if not sources:
        raise InputError("a fitted model must cite the sources of its dataset")
    keep = [i for i, name in enumerate(fit.names) if name != _C0]
    names = [_parameter_name(fit.names[i]) for i in keep]
    mean = [float(fit.mean[i]) for i in keep]
    covariance = [[float(fit.covariance[i][j]) for j in keep] for i in keep]
    has_humidity = _B in fit.names
    rh_min = fit.rh_min_percent if has_humidity and fit.rh_min_percent is not None else 0.0
    rh_max = fit.rh_max_percent if has_humidity and fit.rh_max_percent is not None else PERCENT
    entry: StabilityModelDoc = {
        "model_id": model_id,
        "scope": "product",
        "product_id": product_id,
        "ingredient_id": ingredient_id,
        "package_id": package_id,
        "study": None,
        "rate_law_kind": fit.rate_law_kind,
        "kind_parameters": {"order_n": fit.order_n, "alpha0": fit.alpha0, "avrami_n": fit.avrami_n},
        "reference_temperature_k": fit.reference_temperature_k,
        "reference_rh_percent": fit.reference_rh_percent if has_humidity else None,
        "parameters": {"names": names, "mean": mean, "covariance": covariance},
        "fit": {
            "method": (
                f"WNLLS (scipy least_squares trf) of C = C0 (1 - G(k(T, RH) t)), kind {fit.rate_law_kind}, "
                f"{'converged' if fit.converged else 'not converged'}, AICc {fit.aicc!r}"
            ),
            "n": fit.n_observations,
            "dof": fit.dof,
            "rss": fit.rss_weighted,
            "r2": None,
            "published_ea_j_per_mol": None,
            "ea_gap_j_per_mol": None,
        },
        "validated_range": {
            "temperature_min_k": fit.temperature_min_k,
            "temperature_max_k": fit.temperature_max_k,
            "rh_min_percent": rh_min,
            "rh_max_percent": rh_max,
            "basis": "derived",
            "source_ids": list(sources),
            "to_be_set_by": None,
            "note": "Study range of the fitted dataset (spec 14.5).",
        },
        "max_study_duration_s": fit.max_time_s,
        "covers": [],
        "dataset_sha256": fit.inputs_sha256 or dataset_sha256,
        "measured_degradant_ids": [],
        "status": "reproduces",
        "basis": "derived",
        "source_ids": list(sources),
        "note": (
            "Fitted offline by pharmacy.fit (spec 14); review_state draft until reviewed. "
            + (
                f"C0 fitted ({fit.mean[fit.names.index(_C0)]!r} %) and not kept as a model parameter."
                if _C0 in fit.names
                else f"C0 fixed at {fit.initial_percent!r} %."
            )
        ),
    }
    wrapper = {
        "document_id": "stability-models",
        "schema": _STABILITY_MODELS_SCHEMA,
        "version": "0.0.0",
        "review_state": "draft",
        "notice": "model_document validation wrapper",
        "models": [entry],
    }
    validate_instance(cast(JSONValue, wrapper), _STABILITY_MODELS_SCHEMA, model_id)
    return entry

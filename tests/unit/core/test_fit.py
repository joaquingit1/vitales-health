"""Unit and vector tests of the offline fit (spec 14)."""

import dataclasses
import json
import math
import types
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from scipy.optimize import least_squares as real_least_squares

import pharmacy.fit as fit_module
from pharmacy.config import ConfigBundle, validate_instance
from pharmacy.constants import (
    GAS_CONSTANT_J_PER_MOL_K,
    SECONDS_PER_DAY,
    SECONDS_PER_MINUTE,
    SECONDS_PER_MONTH,
    THERMOCHEMICAL_CALORIE_J,
)
from pharmacy.errors import (
    ComputationError,
    ConfigSchemaError,
    ConvergenceError,
    InputError,
    ModelDomainError,
)
from pharmacy.fit import (
    StudyFit,
    StudyObservation,
    fit_arrhenius_two_stage,
    fit_first_order_loglinear,
    fit_stability_study,
    model_document,
    select_model,
)
from pharmacy.kinetics import conversion_from_theta_array, rate_constant
from pharmacy.types import NumericsSettings, RateLawKind

type Vector = Callable[[str], dict[str, Any]]

T_REF = 298.15
RH_REF = 40.0
REL = 1e-9
HASH = "ab" * 32
SECONDS_PER_WEEK = 7.0 * SECONDS_PER_DAY


def _rel(actual: float, expected: float) -> float:
    return abs(actual / expected - 1.0)


# =========================================================================================
# Two-stage Arrhenius (spec 14.1): vectors
# =========================================================================================


def test_tv_kin_1_full_precision(vector: Vector) -> None:
    v = vector("TV-KIN-1")
    inputs = v["inputs"]
    ours = v["our_recomputation"]
    result = fit_arrhenius_two_stage(inputs["temperatures_k"], inputs["k_per_min"], T_REF)
    assert _rel(result.ea_j_per_mol, ours["foundation_ea_j_per_mol"]) < REL
    assert _rel(result.ea_se_j_per_mol, ours["foundation_ea_se_j_per_mol"]) < REL
    assert _rel(result.ln_a, ours["foundation_ln_a_in_published_k_units"]) < REL
    assert _rel(result.r2, ours["foundation_r2"]) < REL
    ln_k_ref_per_s = result.ln_k_ref - math.log(SECONDS_PER_MINUTE)
    assert _rel(ln_k_ref_per_s, ours["foundation_lnk_ref_at_298_15_k_per_s"]) < REL
    assert _rel(result.ln_k_ref_se, ours["foundation_lnk_ref_se"]) < REL
    assert result.dof == 2
    assert result.covariance[0][1] == result.covariance[1][0]
    assert math.isclose(result.covariance[1][1], result.ea_se_j_per_mol**2, rel_tol=1e-12)
    assert math.isclose(result.covariance[0][0], result.ln_k_ref_se**2, rel_tol=1e-12)


def test_tv_kin_1_against_the_published_value(vector: Vector) -> None:
    v = vector("TV-KIN-1")
    result = fit_arrhenius_two_stage(v["inputs"]["temperatures_k"], v["inputs"]["k_per_min"], T_REF)
    assert (
        abs(result.ea_j_per_mol / 1000.0 - v["expected"]["activation_energy_kj_per_mol"])
        <= v["tolerance"]["abs"]
    )


def test_rate_unit_moves_only_the_intercept(vector: Vector) -> None:
    v = vector("TV-KIN-1")
    per_min = fit_arrhenius_two_stage(v["inputs"]["temperatures_k"], v["inputs"]["k_per_min"], T_REF)
    per_s = fit_arrhenius_two_stage(
        v["inputs"]["temperatures_k"], [k / SECONDS_PER_MINUTE for k in v["inputs"]["k_per_min"]], T_REF
    )
    assert math.isclose(per_s.ea_j_per_mol, per_min.ea_j_per_mol, rel_tol=1e-12)
    assert math.isclose(per_s.ln_k_ref, per_min.ln_k_ref - math.log(SECONDS_PER_MINUTE), rel_tol=1e-12)


@pytest.mark.parametrize(
    "vector_id",
    [
        "TV-KIN-2",
        "TV-KIN-3-1",
        "TV-KIN-3-2",
        "TV-KIN-3-3",
        "TV-KIN-3-4",
        "TV-KIN-3-5",
        "TV-KIN-3-6",
        "TV-KIN-3-7",
    ],
)
def test_arrhenius_vectors_reproduce(vector: Vector, vector_id: str) -> None:
    v = vector(vector_id)
    assert v["status"] == "reproduces"
    inputs = v["inputs"]
    (rate_key,) = [key for key in inputs if key.startswith("k_")]
    result = fit_arrhenius_two_stage(inputs["temperatures_k"], inputs[rate_key], T_REF)
    ours = v["our_recomputation"]
    assert _rel(result.ea_j_per_mol, ours["foundation_ea_j_per_mol"]) < REL
    assert _rel(result.ea_se_j_per_mol, ours["foundation_ea_se_j_per_mol"]) < REL
    assert _rel(result.r2, ours["foundation_r2"]) < REL
    assert (
        abs(result.ea_j_per_mol / 1000.0 - v["expected"]["activation_energy_kj_per_mol"])
        <= v["tolerance"]["abs"]
    )


def test_tv_kin_3x_known_mismatch_asserts_the_refit(vector: Vector) -> None:
    """Known mismatch: the published 55.46 kJ/mol does not follow from the published k
    table; the refit (70.2 +- 14.1 kJ/mol) is asserted and the published value never is."""
    v = vector("TV-KIN-3x")
    assert v["status"] == "known_mismatch"
    result = fit_arrhenius_two_stage(v["inputs"]["temperatures_k"], v["inputs"]["k_per_h"], T_REF)
    assert _rel(result.ea_j_per_mol, 70159.5156212982) < REL
    assert _rel(result.ea_se_j_per_mol, 14130.819212742708) < REL
    assert _rel(result.ea_j_per_mol, v["our_recomputation"]["foundation_ea_j_per_mol"]) < REL


# =========================================================================================
# Two-stage Arrhenius: unit tests
# =========================================================================================


def test_exact_arrhenius_line_is_recovered() -> None:
    temperatures = [303.15, 313.15, 323.15, 333.15]
    rates = [rate_constant(-12.0, 80000.0, 0.0, t, RH_REF, T_REF, RH_REF) for t in temperatures]
    result = fit_arrhenius_two_stage(temperatures, rates, T_REF)
    assert math.isclose(result.ea_j_per_mol, 80000.0, rel_tol=1e-9)
    assert math.isclose(result.ln_k_ref, -12.0, rel_tol=1e-12)
    assert math.isclose(result.ln_a, -12.0 + 80000.0 / (GAS_CONSTANT_J_PER_MOL_K * T_REF), rel_tol=1e-12)
    assert result.r2 == pytest.approx(1.0, abs=1e-12)


def test_flat_rates_have_zero_activation_energy_and_unit_r2() -> None:
    result = fit_arrhenius_two_stage([300.0, 310.0, 320.0], [1e-6, 1e-6, 1e-6], T_REF)
    assert result.ea_j_per_mol == pytest.approx(0.0, abs=1e-6)
    assert result.r2 == 1.0
    assert result.rss == pytest.approx(0.0, abs=1e-24)


@pytest.mark.parametrize(
    ("temperatures", "rates", "match"),
    [
        ([300.0, 310.0], [1e-6, 2e-6, 3e-6], "mismatched lengths"),
        ([300.0, 310.0], [1e-6, 2e-6], "3 distinct temperatures"),
        ([300.0, 300.0, 310.0], [1e-6, 2e-6, 3e-6], "3 distinct temperatures"),
        ([300.0, -310.0, 320.0], [1e-6, 2e-6, 3e-6], "temperatures must be positive"),
        ([300.0, 310.0, 320.0], [1e-6, 0.0, 3e-6], "rate constants must be positive"),
        ([300.0, math.nan, 320.0], [1e-6, 2e-6, 3e-6], "finite"),
        ([300.0, 310.0, 320.0], [1e-6, math.inf, 3e-6], "finite"),
    ],
)
def test_arrhenius_input_errors(temperatures: list[float], rates: list[float], match: str) -> None:
    with pytest.raises(InputError, match=match):
        fit_arrhenius_two_stage(temperatures, rates, T_REF)


def test_arrhenius_reference_errors() -> None:
    with pytest.raises(InputError, match="reference temperature"):
        fit_arrhenius_two_stage([300.0, 310.0, 320.0], [1e-6, 2e-6, 3e-6], 0.0)
    with pytest.raises(InputError, match="gas constant"):
        fit_arrhenius_two_stage([300.0, 310.0, 320.0], [1e-6, 2e-6, 3e-6], T_REF, 0.0)


# =========================================================================================
# Log-linear first order (spec 14.2)
# =========================================================================================


def test_tv_kin_6_free_and_fixed_intercept(vector: Vector) -> None:
    v = vector("TV-KIN-6")
    inputs = v["inputs"]
    expected = v["expected"]
    free = fit_first_order_loglinear(inputs["times_h"], inputs["percent_remaining"])
    assert _rel(free.k, expected["k_free_per_h_full_precision"]) < REL
    assert _rel(free.k_se, expected["k_free_se_per_h_full_precision"]) < REL
    assert _rel(free.c0_percent, expected["c0_free_percent_full_precision"]) < REL
    assert free.c0_se_percent is not None
    assert free.c0_se_percent > 0.0
    assert free.dof == len(inputs["times_h"]) - 2
    fixed = fit_first_order_loglinear(inputs["times_h"], inputs["percent_remaining"], fixed_c0_percent=100.0)
    assert _rel(fixed.k, expected["k_fixed_c0_100_per_h_full_precision"]) < REL
    assert fixed.c0_percent == 100.0
    assert fixed.c0_se_percent is None
    assert fixed.dof == len(inputs["times_h"]) - 1
    # The point above 100 % is kept: its residual is part of the fit.
    assert max(inputs["percent_remaining"]) > 100.0


def test_tv_rad_1_intercept_fixed_at_the_initial_concentration(vector: Vector) -> None:
    v = vector("TV-RAD-1")
    inputs = v["inputs"]
    initial = inputs["concentration_ug_per_ml"][0]
    fixed = fit_first_order_loglinear(
        inputs["dose_kgy"], inputs["concentration_ug_per_ml"], fixed_c0_percent=initial
    )
    assert _rel(fixed.k, v["our_recomputation"]["foundation_k_fixed_intercept_per_kgy"]) < REL
    assert _rel(fixed.k, 0.08232583754344251) < REL
    assert abs(fixed.k - v["expected"]["k_per_kgy"]) <= v["tolerance"]["abs"]
    free = fit_first_order_loglinear(inputs["dose_kgy"], inputs["concentration_ug_per_ml"])
    assert _rel(free.k, v["our_recomputation"]["foundation_k_free_intercept_per_kgy"]) < REL
    # The vector note: the free-intercept value falls outside the 0.0005 tolerance.
    assert abs(free.k - v["expected"]["k_per_kgy"]) > v["tolerance"]["abs"]


def test_first_order_exact_data() -> None:
    x = [0.0, 1.0, 2.0, 4.0]
    y = [98.0 * math.exp(-0.05 * t) for t in x]
    free = fit_first_order_loglinear(x, y)
    assert math.isclose(free.k, 0.05, rel_tol=1e-12)
    assert math.isclose(free.c0_percent, 98.0, rel_tol=1e-12)
    fixed = fit_first_order_loglinear(x, y, fixed_c0_percent=98.0)
    assert math.isclose(fixed.k, 0.05, rel_tol=1e-12)


@pytest.mark.parametrize(
    ("x", "y", "fixed", "match"),
    [
        ([0.0, 1.0], [100.0, 99.0, 98.0], None, "mismatched lengths"),
        ([0.0, 1.0, 2.0], [100.0, 0.0, 98.0], None, "positive"),
        ([0.0, math.nan, 2.0], [100.0, 99.0, 98.0], None, "finite"),
        ([0.0, 1.0], [100.0, 99.0], None, "at least 3 points"),
        ([1.0, 1.0, 1.0], [100.0, 99.0, 98.0], None, "distinct"),
        ([0.0, 1.0], [100.0, 99.0], 0.0, "fixed C0"),
        ([0.0, 1.0], [100.0, 99.0], math.inf, "fixed C0"),
        ([1.0], [99.0], 100.0, "at least 2 points"),
        ([0.0, 0.0], [100.0, 99.0], 100.0, "nonzero abscissa"),
    ],
)
def test_first_order_input_errors(x: list[float], y: list[float], fixed: float | None, match: str) -> None:
    with pytest.raises(InputError, match=match):
        fit_first_order_loglinear(x, y, fixed_c0_percent=fixed)


# =========================================================================================
# Whole-study WNLLS (spec 14.3)
# =========================================================================================

TRUE_LN_K = math.log(5e-9)
TRUE_EA = 85000.0
TRUE_B = 0.035
TRUE_C0 = 99.5
CONDITIONS = ((303.15, 65.0), (313.15, 75.0), (323.15, 60.0), (333.15, 40.0))
WEEKS = (0, 2, 4, 8, 12)
NOISE = 0.02


def _synthetic(
    kind: RateLawKind,
    *,
    with_sd: bool = False,
    numerics: NumericsSettings | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
) -> list[StudyObservation]:
    """Noise-free model values plus a deterministic +-0.02 % alternation (so RSS_w > 0)."""
    observations: list[StudyObservation] = []
    sign = 1.0
    for temperature, rh in CONDITIONS:
        k = rate_constant(TRUE_LN_K, TRUE_EA, TRUE_B, temperature, rh, T_REF, RH_REF)
        for week in WEEKS:
            t = week * SECONDS_PER_WEEK
            alpha = float(
                conversion_from_theta_array(
                    kind, np.array([k * t]), numerics=numerics, alpha0=alpha0, avrami_n=avrami_n
                )[0]
            )
            observations.append(
                StudyObservation(
                    time_s=t,
                    temperature_k=temperature,
                    rh_percent=rh,
                    response_percent=TRUE_C0 * (1.0 - alpha) + sign * NOISE,
                    sd_percent=0.5 if with_sd else None,
                    group=f"{temperature}_{rh}",
                )
            )
            sign = -sign
    return observations


def _assert_recovers(result: StudyFit, n_sigma: float = 4.0) -> None:
    truth = {
        "ln_k_ref_per_s": TRUE_LN_K,
        "ea_j_per_mol": TRUE_EA,
        "b_per_percent_rh": TRUE_B,
        "c0_percent": TRUE_C0,
    }
    for i, name in enumerate(result.names):
        se = math.sqrt(result.covariance[i][i])
        assert abs(result.mean[i] - truth[name]) <= n_sigma * se + 1e-9 * abs(truth[name]), name


@pytest.mark.parametrize("kind", ["F1", "R3", "A2", "F2"])
def test_recovers_known_parameters_with_humidity(kind: RateLawKind) -> None:
    observations = _synthetic(kind)
    result = fit_stability_study(
        observations, kind, T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
    )
    assert result.converged
    assert result.names == ("ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh", "c0_percent")
    assert result.dof == len(observations) - 4
    _assert_recovers(result)
    assert abs(result.mean[1] / TRUE_EA - 1.0) < 0.01
    assert abs(result.mean[2] / TRUE_B - 1.0) < 0.02
    n, p = len(observations), 4
    assert result.aic is not None
    assert result.aicc is not None
    assert result.bic is not None
    assert math.isclose(result.aic, 2 * p + n * math.log(result.rss_weighted / n), rel_tol=1e-12)
    assert math.isclose(result.aicc, result.aic + 2 * p * (p + 1) / (n - p - 1), rel_tol=1e-12)
    assert math.isclose(result.bic, p * math.log(n) + n * math.log(result.rss_weighted / n), rel_tol=1e-12)
    cov = np.array(result.covariance)
    assert np.allclose(cov, cov.T, rtol=1e-12, atol=0.0)
    assert result.n_observations == n
    assert result.temperature_min_k == 303.15
    assert result.temperature_max_k == 333.15
    assert result.rh_min_percent == 40.0
    assert result.rh_max_percent == 75.0
    assert result.max_time_s == 12 * SECONDS_PER_WEEK
    assert result.initial_percent is None


def test_absolute_weights_use_unit_scale() -> None:
    observations = _synthetic("F1", with_sd=True)
    absolute = fit_stability_study(
        observations, "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=True
    )
    relative = fit_stability_study(
        observations, "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
    )
    assert absolute.mean == pytest.approx(relative.mean, rel=1e-9)
    s2 = relative.rss_weighted / relative.dof
    for i in range(4):
        assert math.isclose(relative.covariance[i][i], s2 * absolute.covariance[i][i], rel_tol=1e-6)


def test_fixed_initial_without_humidity_uses_time_zero_mean() -> None:
    observations = _synthetic("F1")
    dry = [
        dataclasses.replace(o, rh_percent=None)
        for o in observations
        if o.rh_percent == 40.0 or o.time_s == 0.0
    ]
    dry += [
        dataclasses.replace(o, rh_percent=None)
        for o in observations
        if o.temperature_k == 323.15 and o.time_s > 0.0
    ]
    result = fit_stability_study(
        dry, "F1", T_REF, RH_REF, fit_humidity=False, fit_initial=False, absolute_weights=False
    )
    assert result.names == ("ln_k_ref_per_s", "ea_j_per_mol")
    zero = [o.response_percent for o in dry if o.time_s == 0.0]
    assert result.initial_percent == pytest.approx(sum(zero) / len(zero), rel=1e-12)
    assert result.rh_min_percent is None


def test_fixed_initial_defaults_to_label_claim_without_time_zero() -> None:
    observations = [o for o in _synthetic("F1") if o.time_s > 0.0]
    result = fit_stability_study(
        observations, "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=False, absolute_weights=False
    )
    assert result.initial_percent == 100.0
    explicit = fit_stability_study(
        observations,
        "F1",
        T_REF,
        RH_REF,
        fit_humidity=True,
        fit_initial=False,
        absolute_weights=False,
        initial_percent=TRUE_C0,
    )
    assert explicit.initial_percent == TRUE_C0
    _assert_recovers(explicit)


def test_fitted_initial_starts_from_the_largest_response_without_time_zero() -> None:
    observations = [o for o in _synthetic("F1") if o.time_s > 0.0]
    result = fit_stability_study(
        observations, "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
    )
    assert result.converged
    _assert_recovers(result)


def test_diffusion_kind_needs_and_uses_numerics(bundle: ConfigBundle) -> None:
    numerics = bundle.settings["numerics"]
    observations = _synthetic("D2", numerics=numerics)
    with pytest.raises(ModelDomainError):
        fit_stability_study(
            observations, "D2", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )
    result = fit_stability_study(
        observations,
        "D2",
        T_REF,
        RH_REF,
        fit_humidity=True,
        fit_initial=True,
        absolute_weights=False,
        numerics=numerics,
    )
    _assert_recovers(result)


@pytest.mark.parametrize(("kind", "alpha0", "avrami_n"), [("B1", 0.05, None), ("An", None, 1.5)])
def test_kind_parameters_pass_through(
    kind: RateLawKind, alpha0: float | None, avrami_n: float | None
) -> None:
    observations = _synthetic(kind, alpha0=alpha0, avrami_n=avrami_n)
    result = fit_stability_study(
        observations,
        kind,
        T_REF,
        RH_REF,
        fit_humidity=True,
        fit_initial=True,
        absolute_weights=False,
        alpha0=alpha0,
        avrami_n=avrami_n,
    )
    assert result.alpha0 == alpha0
    assert result.avrami_n == avrami_n
    _assert_recovers(result)


def test_invalid_kind_parameters_raise_model_domain_error() -> None:
    with pytest.raises(ModelDomainError):
        fit_stability_study(
            _synthetic("F1"), "Fn", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )


def test_groups_without_usable_conversion_fall_back_to_a_generic_start() -> None:
    """Every response above C0 (no observed conversion): the start is ln k* = -ln(t_max)."""
    observations = [
        StudyObservation(t * SECONDS_PER_WEEK, temp, None, 100.0 + 0.1 * i, None, f"g{temp}")
        for i, (t, temp) in enumerate([(1, 300.0), (2, 300.0), (1, 320.0), (2, 320.0), (3, 320.0)])
    ]
    start = fit_module._starting_values(  # private helper exercised directly
        fit_module._study_arrays(observations, fit_humidity=False, absolute_weights=False),
        "F1",
        100.0,
        np.zeros(5),
        None,
        None,
        None,
        None,
    )
    assert start == (-math.log(3 * SECONDS_PER_WEEK), 0.0, 0.0)


def _base(**changes: Any) -> list[StudyObservation]:
    return [dataclasses.replace(o, **changes) for o in _synthetic("F1")]


@pytest.mark.parametrize(
    ("observations", "kwargs", "match"),
    [
        ([], {}, "needs observations"),
        (_base(time_s=-1.0), {}, "non-negative"),
        (_base(temperature_k=0.0), {}, "temperatures must be positive"),
        (_base(rh_percent=101.0), {}, r"\[0, 100\]"),
        (_base(response_percent=math.nan), {}, "finite"),
        (_base(rh_percent=None), {}, "RH of every observation"),
        (_base(sd_percent=0.0), {}, "sd_percent must be positive"),
        (_base(), {"absolute_weights": True}, "absolute weights need sd_percent"),
        (_base(temperature_k=310.0), {}, "2 or more temperatures"),
        (_base(rh_percent=50.0), {}, "2 or more RH values"),
        (_base(time_s=0.0), {}, "after time zero"),
        (_base()[:4], {}, "needs n > p"),
        (_base(), {"fit_initial": False, "initial_percent": -1.0}, "initial_percent"),
        (_base(), {"reference_rh_percent": math.nan}, "reference RH"),
        (_base(), {"reference_temperature_k": -1.0}, "reference temperature"),
    ],
)
def test_study_input_errors(observations: list[StudyObservation], kwargs: dict[str, Any], match: str) -> None:
    arguments: dict[str, Any] = {
        "reference_temperature_k": T_REF,
        "reference_rh_percent": RH_REF,
        "fit_humidity": True,
        "fit_initial": True,
        "absolute_weights": False,
    }
    arguments.update(kwargs)
    with pytest.raises(InputError, match=match):
        fit_stability_study(observations, "F1", **arguments)


def test_mixed_sd_is_rejected() -> None:
    observations = _synthetic("F1", with_sd=True)
    observations[3] = dataclasses.replace(observations[3], sd_percent=None)
    with pytest.raises(InputError, match="every observation has sd_percent or none"):
        fit_stability_study(
            observations, "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )


def test_collinear_design_raises_computation_error() -> None:
    """Two conditions cannot separate Ea from B: J^T W J is singular."""
    observations = [o for o in _synthetic("F1") if o.temperature_k in (313.15, 323.15)]
    with pytest.raises(ComputationError, match="does not identify"):
        fit_stability_study(
            observations, "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )


def test_parameter_without_effect_raises_computation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fit_module, "rate_function", lambda *args, **kwargs: 0.0)
    with pytest.raises(ComputationError, match="does not affect any observation"):
        fit_stability_study(
            _synthetic("F1"), "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )


def test_exact_fit_raises_computation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fit_module, "conversion_from_theta_array", lambda kind, theta, **kw: np.zeros_like(theta)
    )
    observations = [dataclasses.replace(o, response_percent=100.0) for o in _synthetic("F1")]
    with pytest.raises(ComputationError, match="does not affect any observation"):
        fit_stability_study(
            observations,
            "F1",
            T_REF,
            RH_REF,
            fit_humidity=True,
            fit_initial=False,
            absolute_weights=False,
            initial_percent=100.0,
        )


def test_non_finite_model_raises_computation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fit_module, "conversion_from_theta_array", lambda kind, theta, **kw: np.full_like(theta, math.nan)
    )
    with pytest.raises(ComputationError, match="non-finite residual"):
        fit_stability_study(
            _synthetic("F1"), "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )


def test_hard_optimizer_failure_raises_convergence_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(fun: Any, x0: Any, **kwargs: Any) -> types.SimpleNamespace:
        return types.SimpleNamespace(status=-1, x=np.asarray(x0), message="improper input")

    monkeypatch.setattr(fit_module, "least_squares", failing)
    with pytest.raises(ConvergenceError, match="improper input"):
        fit_stability_study(
            _synthetic("F1"), "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
        )


def test_evaluation_limit_reports_not_converged(monkeypatch: pytest.MonkeyPatch) -> None:
    def limited(fun: Any, x0: Any, **kwargs: Any) -> Any:
        return real_least_squares(fun, x0, max_nfev=1, **kwargs)

    monkeypatch.setattr(fit_module, "least_squares", limited)
    result = fit_stability_study(
        _synthetic("F1"), "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
    )
    assert not result.converged


def test_tv_kin_4_known_mismatch_accepts_negative_b(vector: Vector) -> None:
    """Known mismatch (plausibility only): equation S1 of the source is unavailable, so the
    published ln A, Ea and B are never asserted. The fitter must run on the degradant table
    (remaining drug = 100 - degradant) and accept a fitted B < 0 (drier is worse)."""
    v = vector("TV-KIN-4")
    assert v["status"] == "known_mismatch"
    observations = [
        StudyObservation(
            time_s=row["time_weeks"] * SECONDS_PER_WEEK,
            temperature_k=row["temperature_c"] + 273.15,
            rh_percent=float(row["rh_percent"]),
            response_percent=100.0 - row["degradant_dp_o_percent"],
            sd_percent=None,
            group=f"{row['temperature_c']}_{row['rh_percent']}",
        )
        for row in v["inputs"]["observations"]
    ]
    for kind in ("F0", "D1"):
        result = fit_stability_study(
            observations,
            kind,
            T_REF,
            RH_REF,
            fit_humidity=True,
            fit_initial=False,
            absolute_weights=False,
            initial_percent=100.0,
        )
        assert result.converged
        b = result.mean[result.names.index("b_per_percent_rh")]
        assert b < 0.0
        assert result.mean[result.names.index("ea_j_per_mol")] > 0.0


def test_tv_h_5_known_mismatch_table_does_not_reproduce(vector: Vector) -> None:
    """Known mismatch: the Waterman table summarizes an unpublished fuller dataset. Three
    6-month points per lot cannot identify ln k*, Ea and B by WNLLS (n <= p + 1); the
    exactly determined humidity-corrected Arrhenius solve gives a negative Ea for both lots;
    without the humidity term the two-stage Ea of lot 2 (about 13 kcal/mol) is far from the
    published 21 +- 2 kcal/mol. The published values are never asserted."""
    v = vector("TV-H-5")
    assert v["status"] == "known_mismatch"
    conditions = {"25_60": (298.15, 60.0), "30_65": (303.15, 65.0), "40_75": (313.15, 75.0)}
    six_months = 6.0 * SECONDS_PER_MONTH
    kcal = THERMOCHEMICAL_CALORIE_J * 1000.0
    eas: list[float] = []
    for lot in v["inputs"]["lots"]:
        levels = lot["degradant_6_months_percent"]
        keys = sorted(levels)
        observations = [
            StudyObservation(
                six_months, conditions[key][0], conditions[key][1], 100.0 - levels[key], None, key
            )
            for key in keys
        ]
        with pytest.raises(InputError, match="needs n > p"):
            fit_stability_study(
                observations,
                "F0",
                T_REF,
                RH_REF,
                fit_humidity=True,
                fit_initial=False,
                absolute_weights=False,
                initial_percent=100.0,
            )
        temperatures = np.array([conditions[key][0] for key in keys])
        rh = np.array([conditions[key][1] for key in keys])
        log_rates = np.log(np.array([levels[key] for key in keys]) / six_months)
        x = (temperatures - T_REF) / (temperatures * T_REF * GAS_CONSTANT_J_PER_MOL_K)
        exact = np.linalg.solve(np.column_stack([np.ones(3), x, rh - RH_REF]), log_rates)
        assert exact[1] < 0.0
        two_stage = fit_arrhenius_two_stage(
            temperatures.tolist(), [levels[key] / six_months for key in keys], T_REF
        )
        # Plausible size (10 to 40 kcal/mol) but not the published value for lot 2.
        assert 10.0 < two_stage.ea_j_per_mol / kcal < 40.0
        eas.append(two_stage.ea_j_per_mol / kcal)
    lot_2 = v["expected"]["lot_2"]["ea_kcal_per_mol"]
    assert abs(eas[1] - lot_2["mean"]) > lot_2["sd"]


def test_tv_fit_1_unverified(vector: Vector) -> None:
    v = vector("TV-FIT-1")
    assert v["status"] == "unverified"
    pytest.skip(f"TV-FIT-1 unverified: {v['note']}")


# =========================================================================================
# Model selection (spec 14.4)
# =========================================================================================


def _hand_fit(kind: RateLawKind, aicc: float, p: int = 2, **extra: Any) -> StudyFit:
    names = ("ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh", "c0_percent")[:p]
    return StudyFit(
        rate_law_kind=kind,
        names=names,
        mean=tuple(float(i) for i in range(p)),
        covariance=tuple(tuple(1.0 if i == j else 0.0 for j in range(p)) for i in range(p)),
        rss_weighted=1.0,
        dof=10,
        aic=aicc,
        aicc=aicc,
        bic=aicc,
        reference_temperature_k=T_REF,
        reference_rh_percent=RH_REF,
        converged=True,
        **extra,
    )


def test_select_lowest_aicc_outside_the_tie_band() -> None:
    best = _hand_fit("D3", 10.0, p=4)
    assert select_model([_hand_fit("F1", 12.5), best, _hand_fit("F0", 30.0)]) is best


def test_select_ties_go_to_lowest_predicted_potency() -> None:
    condition = StudyObservation(1.0, T_REF, RH_REF, 0.0, None, "long_term")
    low = dataclasses.replace(_hand_fit("F1", 11.9), mean=(0.0, 0.0), initial_percent=100.0)
    high = dataclasses.replace(_hand_fit("F1", 10.0), mean=(-1.0, 0.0), initial_percent=100.0)
    assert select_model([high, low], condition=condition) is low


def test_select_potency_ties_use_parameter_count_then_kind_order() -> None:
    condition = StudyObservation(0.0, T_REF, RH_REF, 0.0, None, "long_term")
    first = dataclasses.replace(_hand_fit("F1", 12.0), initial_percent=100.0)
    later = dataclasses.replace(_hand_fit("A2", 10.0), initial_percent=100.0)
    bigger = dataclasses.replace(_hand_fit("F0", 10.0, p=3), initial_percent=100.0)
    assert select_model([later, bigger, first], condition=condition) is first
    assert select_model([first, dataclasses.replace(first)], condition=condition) is first


def test_select_requires_comparison_condition_and_aicc() -> None:
    with pytest.raises(InputError, match="condition"):
        select_model([_hand_fit("F1", 10.0), _hand_fit("F0", 11.0)])
    with pytest.raises(InputError, match="AICc"):
        select_model([dataclasses.replace(_hand_fit("F1", 10.0), aicc=None)])


def test_select_needs_fits() -> None:
    with pytest.raises(InputError):
        select_model([])


# =========================================================================================
# Model document (spec 14.5)
# =========================================================================================


def _validate(entry: object) -> None:
    wrapper: dict[str, Any] = {
        "document_id": "stability-models",
        "schema": "pharmacy/stability-models.schema.json",
        "version": "1.0.0",
        "review_state": "draft",
        "notice": "test",
        "models": [entry],
    }
    validate_instance(wrapper, "pharmacy/stability-models.schema.json", "test")


def test_model_document_from_a_fit_validates() -> None:
    result = fit_stability_study(
        _synthetic("R3"), "R3", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
    )
    entry = model_document(
        result, "prod_test:api", "prod_test", "api", "original_bottle", HASH, ["MDS14", "MDS01"]
    )
    _validate(entry)
    assert entry["scope"] == "product"
    assert entry["rate_law_kind"] == "R3"
    assert entry["parameters"]["names"] == ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"]
    assert entry["parameters"]["mean"] == list(result.mean[:3])
    assert entry["parameters"]["covariance"] == [list(row[:3]) for row in result.covariance[:3]]
    assert entry["reference_rh_percent"] == RH_REF
    assert entry["validated_range"]["temperature_min_k"] == 303.15
    assert entry["validated_range"]["temperature_max_k"] == 333.15
    assert entry["validated_range"]["rh_min_percent"] == 40.0
    assert entry["validated_range"]["rh_max_percent"] == 75.0
    assert entry["max_study_duration_s"] == 12 * SECONDS_PER_WEEK
    assert entry["fit"]["n"] == len(_synthetic("R3"))
    assert entry["source_ids"] == ["MDS01", "MDS14"]
    assert entry["dataset_sha256"] == result.inputs_sha256
    assert "C0 fitted" in entry["note"]


def test_model_document_without_humidity() -> None:
    observations = [dataclasses.replace(o, rh_percent=None) for o in _synthetic("F1") if o.rh_percent != 75.0]
    result = fit_stability_study(
        observations, "F1", T_REF, RH_REF, fit_humidity=False, fit_initial=False, absolute_weights=False
    )
    entry = model_document(result, "m1", "p1", "i1", None, HASH, ["MDS14"])
    _validate(entry)
    assert entry["reference_rh_percent"] is None
    assert entry["validated_range"]["rh_min_percent"] == 0.0
    assert entry["validated_range"]["rh_max_percent"] == 100.0
    assert entry["package_id"] is None
    assert "C0 fixed" in entry["note"]


def test_model_document_input_errors() -> None:
    complete = _hand_fit(
        "F1",
        10.0,
        p=4,
        n_observations=20,
        temperature_min_k=300.0,
        temperature_max_k=330.0,
        max_time_s=1e6,
        initial_percent=None,
    )
    with pytest.raises(InputError, match="study description"):
        model_document(_hand_fit("F1", 10.0), "m1", "p1", "i1", None, HASH, ["MDS14"])
    with pytest.raises(InputError, match="64 lower-case"):
        model_document(complete, "m1", "p1", "i1", None, "AB" * 32, ["MDS14"])
    with pytest.raises(InputError, match="64 lower-case"):
        model_document(complete, "m1", "p1", "i1", None, "ab", ["MDS14"])
    with pytest.raises(InputError, match="cite the sources"):
        model_document(complete, "m1", "p1", "i1", None, HASH, [])
    bad_name = dataclasses.replace(
        complete, names=("ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh", "bogus")
    )
    with pytest.raises(InputError, match="bogus"):
        model_document(bad_name, "m1", "p1", "i1", None, HASH, ["MDS14"])
    entry = model_document(complete, "m1", "p1", "i1", None, HASH, ["MDS14"])
    assert entry["parameters"]["names"] == ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"]
    with pytest.raises(ConfigSchemaError):
        model_document(complete, "not a valid id", "p1", "i1", None, HASH, ["MDS14"])
    with pytest.raises(ConfigSchemaError):
        model_document(complete, "m1", "p1", "i1", None, HASH, ["not-a-source"])


def test_model_document_round_trips_through_json() -> None:
    result = fit_stability_study(
        _synthetic("F1"), "F1", T_REF, RH_REF, fit_humidity=True, fit_initial=True, absolute_weights=False
    )
    entry = model_document(result, "m1", "p1", "i1", None, HASH, ["MDS14"])
    assert json.loads(json.dumps(entry, allow_nan=False)) == entry


def test_non_finite_covariance_raises_computation_error() -> None:
    jacobian = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    with pytest.raises(ComputationError, match="not finite and positive"):
        fit_module._covariance(jacobian, math.inf)

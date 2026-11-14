"""Unit tests of pharmacy.kinetics (spec 5, 6.2, 8): vectors, analytic solutions for every
rate-law kind, inverse round trips, the humidity integral against mpmath, ODE convergence,
the vectorized evaluation against hand-computed sums, regimes and placards."""

import copy
import dataclasses
import math
from collections.abc import Callable, Mapping
from typing import Any

import mpmath as mp
import numpy as np
import pytest

from pharmacy import kinetics, microclimate
from pharmacy.config import ConfigBundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, SECONDS_PER_DAY, SECONDS_PER_HOUR, ZERO_CELSIUS_K
from pharmacy.errors import ComputationError, ConvergenceError, InputError, ModelDomainError
from pharmacy.types import (
    EngineSettingsDoc,
    GapRecord,
    LotExposure,
    NumericsSettings,
    PackageDoc,
    RateLawKind,
    Segment,
    StorageZoneDoc,
    ZoneHistory,
)
from tests.unit.core import builders as b

R = GAS_CONSTANT_J_PER_MOL_K
T_REF = 298.15
RH_REF = 40.0

KINDS: tuple[tuple[RateLawKind, dict[str, float]], ...] = (
    ("F0", {}),
    ("F1", {}),
    ("F2", {}),
    ("Fn", {"order_n": 0.5}),
    ("Fn", {"order_n": 2.5}),
    ("R2", {}),
    ("R3", {}),
    ("A2", {}),
    ("A3", {}),
    ("A4", {}),
    ("An", {"avrami_n": 0.7}),
    ("An", {"avrami_n": 1.6}),
    ("B1", {"alpha0": 0.01}),
    ("D1", {}),
    ("D2", {}),
    ("D3", {}),
    ("D4", {}),
)


@pytest.fixture(scope="module")
def numerics(bundle: ConfigBundle) -> NumericsSettings:
    return bundle.settings["numerics"]


@pytest.fixture(scope="module")
def settings(bundle: ConfigBundle) -> EngineSettingsDoc:
    return bundle.settings


@pytest.fixture(scope="module")
def zones(bundle: ConfigBundle) -> dict[str, StorageZoneDoc]:
    return {zone["zone_id"]: zone for zone in bundle.zones["zones"]}


@pytest.fixture(scope="module")
def packages(bundle: ConfigBundle) -> dict[str, PackageDoc]:
    return {package["package_id"]: package for package in bundle.packages["packages"]}


def _vector(get: Callable[[str], dict[str, Any]], vector_id: str) -> dict[str, Any]:
    return get(vector_id)


# =========================================================================================
# Vectors (spec 15.1)
# =========================================================================================


def test_tv_int_1_integrated_rate_and_order_invariance(
    vector: Callable[[str], dict[str, Any]], numerics: NumericsSettings
) -> None:
    v = _vector(vector, "TV-INT-1")
    inputs = v["inputs"]
    ln_k = math.log(inputs["k_ref_per_s"])
    temperatures = [s["temperature_k"] for s in inputs["segments"]]
    durations = [s["duration_s"] for s in inputs["segments"]]
    theta = kinetics.integrated_rate(
        ln_k,
        inputs["activation_energy_j_per_mol"],
        0.0,
        temperatures,
        [50.0, 50.0],
        durations,
        inputs["t_ref_k"],
        50.0,
    )
    reversed_theta = kinetics.integrated_rate(
        ln_k,
        inputs["activation_energy_j_per_mol"],
        0.0,
        temperatures[::-1],
        [50.0, 50.0],
        durations[::-1],
        inputs["t_ref_k"],
        50.0,
    )
    expected = v["expected"]
    assert theta == pytest.approx(expected["theta_full_precision"], rel=1e-9)
    assert reversed_theta == pytest.approx(theta, rel=1e-15)
    alpha = kinetics.conversion_from_theta("F1", theta, numerics=numerics)
    assert inputs["c0_percent"] * (1.0 - alpha) == pytest.approx(
        expected["potency_percent_full_precision"], rel=1e-9
    )
    assert round(theta, 7) == pytest.approx(expected["theta"], abs=1e-7)


def test_tv_int_2_constant_mkt_gives_the_same_potency(
    vector: Callable[[str], dict[str, Any]], numerics: NumericsSettings
) -> None:
    v = _vector(vector, "TV-INT-2")
    inputs = v["inputs"]
    theta = kinetics.integrated_rate(
        math.log(inputs["k_ref_per_s"]),
        inputs["activation_energy_j_per_mol"],
        0.0,
        [inputs["mkt_k_full_precision"]],
        [50.0],
        [inputs["duration_s"]],
        inputs["t_ref_k"],
        50.0,
    )
    potency = inputs["c0_percent"] * (1.0 - kinetics.conversion_from_theta("F1", theta, numerics=numerics))
    assert potency == pytest.approx(v["expected"]["potency_percent_full_precision"], rel=1e-9)


def test_tv_int_4_ode_reference(vector: Callable[[str], dict[str, Any]]) -> None:
    v = _vector(vector, "TV-INT-4")
    inputs = v["inputs"]
    ks = [
        kinetics.rate_constant(
            math.log(inputs["k_ref_per_s"]),
            inputs["activation_energy_j_per_mol"],
            0.0,
            s["temperature_k"],
            50.0,
            inputs["t_ref_k"],
            50.0,
        )
        for s in inputs["segments"]
    ]
    alpha = kinetics.integrate_ode_reference("F1", ks, [s["duration_s"] for s in inputs["segments"]])
    potency = inputs["c0_percent"] * (1.0 - alpha)
    assert potency == pytest.approx(
        v["expected"]["potency_percent_full_precision"], rel=v["tolerance"]["rel"]
    )


def test_tv_kin_3e_t90_from_rate_constant(vector: Callable[[str], dict[str, Any]]) -> None:
    v = _vector(vector, "TV-KIN-3e")
    inputs = v["inputs"]
    recomputed = v["our_recomputation"]
    names = {"Amoxicillin": "amoxicillin", "Clavulanate": "clavulanate"}
    for case in inputs["cases"]:
        key = names[case["series"].split()[0]]
        ln_k_ref = math.log(case["k_ref_per_h"] / SECONDS_PER_HOUR)
        for celsius in case["temperatures_c"]:
            k = kinetics.rate_constant(
                ln_k_ref,
                case["activation_energy_j_per_mol"],
                0.0,
                celsius + ZERO_CELSIUS_K,
                50.0,
                inputs["reference_temperature_k"],
                50.0,
            )
            t90_h = math.log(10.0 / 9.0) / k / SECONDS_PER_HOUR
            label = f"{int(celsius)}_c"
            assert t90_h == pytest.approx(recomputed[f"foundation_{key}_t90_h"][label], rel=1e-9)
            assert t90_h == pytest.approx(v["expected"][f"{key}_t90_h"][label], rel=v["tolerance"]["rel"])


def test_tv_kin_5_humidity_term_as_fraction(vector: Callable[[str], dict[str, Any]]) -> None:
    v = _vector(vector, "TV-KIN-5")
    inputs = v["inputs"]
    ea = inputs["activation_energy_j_per_mol"]
    ln_k_ref = math.log(inputs["a_per_h"] / SECONDS_PER_HOUR) - ea / (R * T_REF)
    b_per_percent = inputs["m_humidity"] / 100.0
    for index, condition in enumerate(inputs["conditions"]):
        k = (
            kinetics.rate_constant(
                ln_k_ref, ea, b_per_percent, condition["temperature_k"], condition["rh_percent"], T_REF, 0.0
            )
            * SECONDS_PER_HOUR
        )
        assert k == pytest.approx(v["our_recomputation"]["foundation_k_per_h"][index], rel=1e-9)
        assert k == pytest.approx(v["expected"]["k_per_h"][index], rel=v["tolerance"]["rel"])


def test_tv_h_2_known_mismatch_asserts_1_296_years(vector: Callable[[str], dict[str, Any]]) -> None:
    v = _vector(vector, "TV-H-2")
    assert v["status"] == "known_mismatch"
    inputs = v["inputs"]
    k60 = kinetics.rate_constant(
        -20.0, 90000.0, inputs["b_per_percent_rh"], T_REF, inputs["rh_from_percent"], T_REF, 60.0
    )
    k75 = kinetics.rate_constant(
        -20.0, 90000.0, inputs["b_per_percent_rh"], T_REF, inputs["rh_to_percent"], T_REF, 60.0
    )
    years = inputs["shelf_life_years_at_60"] * k60 / k75
    assert years == pytest.approx(v["our_recomputation"]["foundation_shelf_life_years"], rel=1e-9)
    assert years == pytest.approx(v["expected"]["shelf_life_years_at_75"], abs=1e-3)
    assert abs(years - 1.2) > 0.09  # the published 1.2 years does not reproduce


def test_tv_h_3_humidity_corrected_arrhenius(vector: Callable[[str], dict[str, Any]]) -> None:
    v = _vector(vector, "TV-H-3")
    inputs = v["inputs"]
    slope = inputs["minus_ea_over_r_k"]
    b_rh = inputs["b_per_percent_rh"]
    ln_k_ref = slope / T_REF + b_rh * 60.0 + inputs["intercept_ln_k"]
    for index, condition in enumerate(inputs["conditions"]):
        k = kinetics.rate_constant(
            ln_k_ref, -slope * R, b_rh, condition["temperature_k"], condition["rh_percent"], T_REF, 60.0
        )
        assert k == pytest.approx(v["our_recomputation"]["foundation_k"][index], rel=1e-9)
        assert k == pytest.approx(v["expected"]["k"][index], rel=v["tolerance"]["rel"])


def test_tv_h_4_open_dish_shelf_lives(vector: Callable[[str], dict[str, Any]]) -> None:
    v = _vector(vector, "TV-H-4")
    inputs = v["inputs"]
    ea_cal = inputs["activation_energy_kcal_per_mol"] * 1000.0
    r_cal = inputs["gas_constant_cal_per_mol_k"]
    b_rh = inputs["b_per_percent_rh"]
    ln_k_ref = inputs["ln_a"] - ea_cal / (r_cal * T_REF) + b_rh * 60.0
    for index, condition in enumerate(inputs["conditions"]):
        k_per_day = kinetics.rate_constant(
            ln_k_ref,
            ea_cal,
            b_rh,
            condition["temperature_c"] + ZERO_CELSIUS_K,
            condition["rh_percent"],
            T_REF,
            60.0,
            gas_constant_j_per_mol_k=r_cal,
        )
        days = inputs["specification_percent"] / k_per_day
        value = days if condition["report_unit"] == "days" else days / 365.25
        assert value == pytest.approx(v["our_recomputation"]["foundation_values"][index], rel=1e-9)
        assert value == pytest.approx(v["expected"]["shelf_life"][index]["value"], rel=v["tolerance"]["rel"])


# =========================================================================================
# Rate constant and integrated rate (spec 5.1, 5.2)
# =========================================================================================


def test_rate_constant_reference_point_and_form() -> None:
    assert kinetics.rate_constant(-18.0, 80000.0, 0.03, T_REF, RH_REF, T_REF, RH_REF) == pytest.approx(
        math.exp(-18.0), rel=1e-15
    )
    t = 310.0
    expected = math.exp(-18.0 - 80000.0 / R * (1.0 / t - 1.0 / T_REF) + 0.03 * (55.0 - RH_REF))
    assert kinetics.rate_constant(-18.0, 80000.0, 0.03, t, 55.0, T_REF, RH_REF) == pytest.approx(
        expected, rel=1e-13
    )


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_rate_constant_rejects_non_positive_temperatures(bad: float) -> None:
    with pytest.raises(ModelDomainError):
        kinetics.rate_constant(-18.0, 80000.0, 0.0, bad, 50.0, T_REF, RH_REF)
    with pytest.raises(ModelDomainError):
        kinetics.rate_constant(-18.0, 80000.0, 0.0, T_REF, 50.0, bad, RH_REF)


def test_rate_constant_rejects_non_finite_inputs_and_overflow() -> None:
    with pytest.raises(ModelDomainError):
        kinetics.rate_constant(math.nan, 80000.0, 0.0, T_REF, 50.0, T_REF, RH_REF)
    with pytest.raises(ComputationError):
        kinetics.rate_constant(800.0, 0.0, 0.0, T_REF, 50.0, T_REF, RH_REF)


def test_integrated_rate_empty_mismatch_and_negative_duration() -> None:
    assert kinetics.integrated_rate(-18.0, 1e5, 0.0, [], [], [], T_REF, RH_REF) == 0.0
    with pytest.raises(InputError):
        kinetics.integrated_rate(-18.0, 1e5, 0.0, [300.0], [50.0, 50.0], [1.0], T_REF, RH_REF)
    with pytest.raises(InputError):
        kinetics.integrated_rate(-18.0, 1e5, 0.0, [300.0], [50.0], [-1.0], T_REF, RH_REF)


def test_integrated_rate_split_invariance() -> None:
    whole = kinetics.integrated_rate(-18.0, 1e5, 0.02, [305.0], [60.0], [86400.0], T_REF, RH_REF)
    parts = kinetics.integrated_rate(-18.0, 1e5, 0.02, [305.0] * 4, [60.0] * 4, [21600.0] * 4, T_REF, RH_REF)
    assert parts == pytest.approx(whole, rel=1e-14)


# =========================================================================================
# Model kinds (spec 5.3)
# =========================================================================================


def _closed_alpha(kind: str, theta: float, params: Mapping[str, float]) -> float:
    """Independent closed forms (mpmath at 40 digits) for the kinds with explicit G."""
    mp.mp.dps = 60
    th = mp.mpf(theta)
    if kind == "F0":
        return float(min(th, 1))
    if kind == "F1":
        return float(-mp.expm1(-th))
    if kind == "F2":
        return float(th / (1 + th))
    if kind == "Fn":
        n = mp.mpf(params["order_n"])
        if n < 1 and th >= 1 / (1 - n):
            return 1.0
        return float(-mp.expm1(mp.log1p((n - 1) * th) / (1 - n)))
    if kind == "R2":
        return float(1 - (1 - min(th, 1)) ** 2)
    if kind == "R3":
        return float(1 - (1 - min(th, 1)) ** 3)
    if kind in ("A2", "A3", "A4", "An"):
        m = {"A2": 2, "A3": 3, "A4": 4}.get(kind) or mp.mpf(params["avrami_n"])
        return float(-mp.expm1(-(th**m)))
    if kind == "B1":
        a0 = mp.mpf(params["alpha0"])
        return float(a0 * -mp.expm1(-th) / (a0 + (1 - a0) * mp.e ** (-th)))
    if kind == "D1":
        return float(mp.sqrt(min(th, 1)))
    if kind == "D3":
        return float(1 - (1 - mp.sqrt(min(th, 1))) ** 3)
    raise AssertionError(kind)


def _g_mp(kind: str, alpha: float) -> mp.mpf:
    mp.mp.dps = 60
    a = mp.mpf(alpha)
    if kind == "D2":
        return (1 - a) * mp.log1p(-a) + a
    return 1 - 2 * a / 3 - mp.e ** (mp.mpf(2) / 3 * mp.log1p(-a))


@pytest.mark.parametrize(("kind", "params"), KINDS)
def test_conversion_matches_analytic_forms(
    kind: RateLawKind, params: dict[str, float], numerics: NumericsSettings
) -> None:
    for theta in (0.0, 1e-9, 1e-4, 0.05, 0.3, 0.7, 0.99, 1.5, 4.0):
        alpha = kinetics.conversion_from_theta(kind, theta, numerics=numerics, **params)
        if kind in ("D2", "D4"):
            complete = 1.0 if kind == "D2" else 1.0 / 3.0
            if theta >= complete:
                assert alpha == 1.0
            elif theta > 0.0:
                assert float(_g_mp(kind, alpha)) == pytest.approx(theta, rel=1e-12)
            else:
                assert alpha == 0.0
            continue
        expected = _closed_alpha(kind, theta, params)
        assert alpha == pytest.approx(expected, rel=1e-13, abs=1e-300)


@pytest.mark.parametrize(("kind", "params"), KINDS)
def test_inverse_round_trip(kind: RateLawKind, params: dict[str, float], numerics: NumericsSettings) -> None:
    for alpha in (1e-12, 1e-6, 1e-3, 0.05, 0.0999, 0.1, 0.2, 0.5, 0.9, 0.999):
        theta = kinetics.theta_from_conversion(kind, alpha, **params)
        back = kinetics.conversion_from_theta(kind, theta, numerics=numerics, **params)
        assert back == pytest.approx(alpha, rel=1e-11)


@pytest.mark.parametrize(("kind", "params"), KINDS)
def test_array_form_is_elementwise_identical(
    kind: RateLawKind, params: dict[str, float], numerics: NumericsSettings
) -> None:
    thetas = np.array([0.0, 1e-12, 1e-5, 0.01, 0.2, 0.32, 0.6, 1.0, 2.0, 30.0])
    array = kinetics.conversion_from_theta_array(kind, thetas, numerics=numerics, **params)
    scalars = [kinetics.conversion_from_theta(kind, float(t), numerics=numerics, **params) for t in thetas]
    assert array.tolist() == scalars
    assert bool(np.all(np.diff(array) >= 0.0))


@pytest.mark.parametrize(("kind", "params"), KINDS)
def test_rate_function_is_reciprocal_slope_of_g(kind: RateLawKind, params: dict[str, float]) -> None:
    for alpha in (0.05, 0.3, 0.6):
        h = 1e-6
        slope = (
            kinetics.theta_from_conversion(kind, alpha + h, **params)
            - kinetics.theta_from_conversion(kind, alpha - h, **params)
        ) / (2 * h)
        assert kinetics.rate_function(kind, alpha, **params) == pytest.approx(1.0 / slope, rel=1e-6)


def test_completion_theta_per_kind() -> None:
    assert kinetics.completion_theta("F0") == 1.0
    assert kinetics.completion_theta("D4") == pytest.approx(1.0 / 3.0)
    assert kinetics.completion_theta("Fn", order_n=0.5) == pytest.approx(2.0)
    assert math.isinf(kinetics.completion_theta("F1"))
    assert math.isinf(kinetics.completion_theta("Fn", order_n=2.0))
    for kind in ("R2", "R3", "D1", "D2", "D3"):
        assert kinetics.completion_theta(kind) == 1.0


def test_d2_d4_inverse_at_tiny_and_near_complete_theta(numerics: NumericsSettings) -> None:
    alpha = kinetics.conversion_from_theta("D2", 6.806149317551839e-87, numerics=numerics)
    assert alpha == pytest.approx(math.sqrt(2 * 6.806149317551839e-87), rel=1e-12)
    alpha = kinetics.conversion_from_theta("D4", 1e-80, numerics=numerics)
    assert alpha == pytest.approx(3.0 * math.sqrt(1e-80), rel=1e-12)
    # theta = 0.5 used to stall at alpha = 1 (g' infinite there); the bracket now bisects.
    alpha = kinetics.conversion_from_theta("D2", 0.5, numerics=numerics)
    assert float(_g_mp("D2", alpha)) == pytest.approx(0.5, rel=1e-13)
    alpha = kinetics.conversion_from_theta("D4", 0.3333, numerics=numerics)
    assert float(_g_mp("D4", alpha)) == pytest.approx(0.3333, rel=1e-12)


def test_d2_needs_numerics_settings() -> None:
    for kind in ("D2", "D4"):
        with pytest.raises(ModelDomainError):
            kinetics.conversion_from_theta(kind, 0.1)


def test_newton_inverse_reports_non_convergence(numerics: NumericsSettings) -> None:
    tight: NumericsSettings = copy.deepcopy(numerics)
    tight["series_max_terms"]["value"] = 1
    with pytest.raises(ConvergenceError):
        kinetics.conversion_from_theta("D2", 1e-6, numerics=tight)


@pytest.mark.parametrize(
    ("kind", "params", "theta"),
    [
        ("F1", {}, -1e-3),
        ("F1", {}, math.nan),
        ("F1", {}, math.inf),
        ("Fn", {}, 0.1),
        ("Fn", {"order_n": 1.0}, 0.1),
        ("Fn", {"order_n": 0.0}, 0.1),
        ("An", {}, 0.1),
        ("An", {"avrami_n": 0.0}, 0.1),
        ("B1", {}, 0.1),
        ("B1", {"alpha0": 1.0}, 0.1),
        ("B1", {"alpha0": 0.0}, 0.1),
    ],
)
def test_conversion_domain_errors(
    kind: RateLawKind, params: dict[str, float], theta: float, numerics: NumericsSettings
) -> None:
    with pytest.raises(ModelDomainError):
        kinetics.conversion_from_theta(kind, theta, numerics=numerics, **params)


def test_unknown_kind_and_theta_from_conversion_domain() -> None:
    with pytest.raises(ModelDomainError):
        kinetics.conversion_from_theta("Z9", 0.1)  # type: ignore[arg-type]
    with pytest.raises(ModelDomainError):
        kinetics.theta_from_conversion("F1", 1.0)
    with pytest.raises(ModelDomainError):
        kinetics.theta_from_conversion("F1", -0.1)
    assert kinetics.theta_from_conversion("F0", 1.0) == 1.0
    assert kinetics.theta_from_conversion("D2", 1.0) == pytest.approx(1.0)
    with pytest.raises(ModelDomainError):
        kinetics.rate_function("F1", 1.5)
    with pytest.raises(ModelDomainError):
        kinetics.rate_function("D1", 0.0)


def test_theta_at_full_conversion_and_other_edges() -> None:
    assert kinetics.theta_from_conversion("Fn", 1.0, order_n=0.5) == pytest.approx(2.0)
    assert kinetics.theta_from_conversion("R3", 1.0) == 1.0
    assert kinetics.theta_from_conversion("D3", 1.0) == 1.0
    with pytest.raises(ModelDomainError):
        kinetics.rate_constant(-18.0, 8e4, 0.0, T_REF, 50.0, T_REF, RH_REF, gas_constant_j_per_mol_k=0.0)
    with pytest.raises(ComputationError):
        kinetics.integrated_rate(700.0, 0.0, 0.0, [T_REF, T_REF], [50.0, 50.0], [1e308, 1e308], T_REF, RH_REF)


def test_rate_function_edges() -> None:
    assert kinetics.rate_function("F0", 0.4) == 1.0
    assert kinetics.rate_function("A2", 0.0) == 0.0
    assert kinetics.rate_function("An", 0.0, avrami_n=1.0) == 1.0
    assert kinetics.rate_function("A3", 1.0) == 0.0
    with pytest.raises(ModelDomainError):
        kinetics.rate_function("An", 0.0, avrami_n=0.5)
    for kind in ("D2", "D3", "D4"):
        assert kinetics.rate_function(kind, 1.0) == 0.0


# =========================================================================================
# Humidity integral (spec 5.4)
# =========================================================================================


def _j_reference(x: float, duration: float, tau: float) -> mp.mpf:
    mp.mp.dps = 40
    xx, d, t = mp.mpf(x), mp.mpf(duration), mp.mpf(tau)
    return mp.quad(lambda s: mp.e ** (xx * mp.e ** (-s / t)), [0, d])


@pytest.mark.parametrize("x", [-60.0, -20.0, -5.5, -5.0, -4.9, -1.0, -1e-6, 1e-6, 0.4, 3.0, 12.0])
@pytest.mark.parametrize("delta", [1e-9, 1e-5, 2e-4, 3e-3, 0.02, 0.09, 0.3, 2.0, 40.0])
def test_humidity_integral_matches_mpmath(x: float, delta: float) -> None:
    tau = 1000.0
    got = kinetics.humidity_integral(x, delta * tau, tau)
    ref = _j_reference(x, delta * tau, tau)
    assert abs(mp.mpf(got) - ref) <= 1e-12 * abs(ref)  # spec 5.4 requires 1e-12 relative for |x| <= 50


def test_humidity_integral_limits_and_errors() -> None:
    assert kinetics.humidity_integral(0.7, 100.0, math.inf) == pytest.approx(100.0 * math.exp(0.7), rel=1e-15)
    assert kinetics.humidity_integral(0.7, 100.0, 0.0) == 100.0
    assert kinetics.humidity_integral(0.0, 100.0, 50.0) == 100.0
    assert kinetics.humidity_integral(0.7, 0.0, 50.0) == 0.0
    for args in ((math.nan, 1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, -1.0), (1.0, 1.0, math.nan)):
        with pytest.raises(ModelDomainError):
            kinetics.humidity_integral(*args)
    with pytest.raises(ComputationError):
        kinetics.humidity_integral(800.0, 1.0, math.inf)
    with pytest.raises(ConvergenceError):
        kinetics.humidity_integral(4.0, 1e4, 10.0, max_terms=3)


def test_humidity_integral_long_segment_does_not_underflow() -> None:
    got = kinetics.humidity_integral(-30.0, 1e6, 1.0)
    ref = _j_reference(-30.0, 60.0, 1.0) + (1e6 - 60.0)
    assert got == pytest.approx(float(ref), rel=1e-12)


# =========================================================================================
# ODE reference (spec 5.9)
# =========================================================================================


@pytest.mark.parametrize(("kind", "params"), KINDS)
def test_ode_reference_agrees_with_closed_form(
    kind: RateLawKind, params: dict[str, float], numerics: NumericsSettings
) -> None:
    ks = [1e-6, 3e-6, 5e-7]
    ds = [2e5, 1e5, 3e5]
    theta = math.fsum(k * d for k, d in zip(ks, ds, strict=True))
    closed = kinetics.conversion_from_theta(kind, theta, numerics=numerics, **params)
    ode = kinetics.integrate_ode_reference(kind, ks, ds, **params)
    assert ode == pytest.approx(closed, abs=1e-8)


def test_ode_reference_input_errors() -> None:
    with pytest.raises(InputError):
        kinetics.integrate_ode_reference("F1", [1e-6], [1.0, 2.0])
    with pytest.raises(InputError):
        kinetics.integrate_ode_reference("F1", [-1e-6], [1.0])


def test_ode_reference_stops_at_completion() -> None:
    assert kinetics.integrate_ode_reference("F0", [1e-5, 1e-5], [1e5, 1e5]) == pytest.approx(1.0)
    # Idle segments are skipped, and the march stops once conversion is complete.
    assert kinetics.integrate_ode_reference(
        "R2", [0.0, 1e-5, 2e-5, 1e-5], [5.0, 0.0, 1e5, 1e5]
    ) == pytest.approx(1.0)
    with pytest.raises(InputError):
        kinetics.integrate_ode_reference("F1", [math.inf], [1.0])


# =========================================================================================
# Vectorized evaluation (spec 5.5 to 5.8, 6, 8.2)
# =========================================================================================


def _steps(
    segments: tuple[Segment, ...], trace: Any, zone_ids: list[str], tracks: bool
) -> dict[str, tuple[float, ...]]:
    return {
        z: microclimate.unit_step_response(
            segments, trace.time_constant_s, z, first_start_tracks_outside=tracks
        )
        for z in zone_ids
    }


def _evaluate(
    model: Any, segments: list[Segment], draws: Any, zones: Any, packages: Any, settings: Any
) -> Any:
    exposure = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(exposure.segments, packages)
    tracks = microclimate.initial_rh_of_first_package(exposure.segments, packages) is None
    zone_ids = sorted({s.zone_id for s in segments} | set(draws.zone_ln_multiplier))
    steps = _steps(exposure.segments, trace, zone_ids, tracks)
    return (
        kinetics.evaluate_draws(model, exposure, trace, steps, draws, zones, packages, settings),
        trace,
        steps,
    )


def test_sealed_blister_matches_hand_sum(
    zones: Any, packages: Any, settings: EngineSettingsDoc, numerics: NumericsSettings
) -> None:
    model = b.thermal_model(ln_k=-17.0, ea=85000.0, b=0.03)
    segments = [
        b.segment(0.0, 3600.0, temperature=300.0, rh=60.0, dose=1e-9),
        b.segment(3600.0, 10800.0, temperature=305.0, rh=20.0, dose=2e-9),
        b.segment(10800.0, 12600.0, temperature=290.0, rh=90.0, dose=0.0),
    ]
    draws = b.fixed_draws(model, ["station_kit"])
    result, _, _ = _evaluate(model, segments, draws, zones, packages, settings)
    sealed = packages["original_blister"]["moisture_model"]["sealed_rh_percent"]["value"]
    expected = math.fsum(
        kinetics.rate_constant(-17.0, 85000.0, 0.03, s.temperature_k, sealed, T_REF, RH_REF)
        * (s.t1_s - s.t0_s)
        for s in segments
    )
    thermal, radiolysis = (0, 1) if model.pathways[0].driver == "thermal" else (1, 0)
    assert result.theta[0, thermal] == pytest.approx(expected, rel=1e-13)
    dose = math.fsum(s.dose_rate_gy_per_s * (s.t1_s - s.t0_s) for s in segments)
    assert result.theta[0, radiolysis] == pytest.approx(math.exp(-14.0) * dose, rel=1e-13)
    assert result.alpha[0] == pytest.approx(-math.expm1(-expected), rel=1e-13)
    assert result.potency_total_percent[0] == pytest.approx(100.0 * math.exp(-expected), rel=1e-13)
    assert result.potency_active_percent[0] == result.potency_total_percent[0]
    assert result.radiation_loss_fraction[0] == pytest.approx(-math.expm1(-math.exp(-14.0) * dose), rel=1e-13)
    # Degradant on a molar basis (yield 1): C0 times the conversion it caused.
    assert result.degradant_percent[0, thermal] == pytest.approx(100.0 * result.alpha[0], rel=1e-12)


def test_radiation_enters_potency_only_when_configured(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = b.thermal_model(ln_k=-30.0, b=None)
    segments = [b.segment(0.0, 86400.0, dose=1e-6)]
    draws = b.fixed_draws(model, ["station_kit"])
    off, _, _ = _evaluate(model, segments, draws, zones, packages, settings)
    on_settings = copy.deepcopy(settings)
    on_settings["radiation"]["include_in_potency"] = True
    on, _, _ = _evaluate(model, segments, draws, zones, packages, on_settings)
    theta_rad = math.exp(-14.0) * 1e-6 * 86400.0
    assert on.alpha[0] > off.alpha[0]
    assert on.alpha[0] == pytest.approx(-math.expm1(-(theta_rad + float(off.theta[0, 0]))), rel=1e-12)


def test_first_order_bottle_uses_the_humidity_integral(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = b.thermal_model(ln_k=-17.0, ea=85000.0, b=0.04, with_radiolysis=False)
    segments = [
        b.segment(0.0, 86400.0 * 3, package="repackaged_bag", temperature=300.0, rh=70.0),
        b.segment(86400.0 * 3, 86400.0 * 4, package="repackaged_bag", temperature=296.0, rh=30.0),
    ]
    draws = b.fixed_draws(model, ["station_kit"])
    result, trace, _ = _evaluate(model, segments, draws, zones, packages, settings)
    expected = 0.0
    for index, s in enumerate(segments):
        k_out = kinetics.rate_constant(
            -17.0, 85000.0, 0.04, s.temperature_k, s.relative_humidity_percent, T_REF, RH_REF
        )
        x = 0.04 * (trace.rh_in_start_high[index] - s.relative_humidity_percent)
        expected += k_out * kinetics.humidity_integral(x, s.t1_s - s.t0_s, trace.time_constant_s[index])
    assert result.theta[0, 0] == pytest.approx(expected, rel=1e-12)


def test_rh_offsets_follow_the_step_response(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(ln_k=-17.0, ea=85000.0, b=0.04, with_radiolysis=False)
    segments = [
        b.segment(0.0, 86400.0, package="repackaged_bag", temperature=300.0, rh=70.0),
        b.segment(86400.0, 2 * 86400.0, package="repackaged_bag", temperature=300.0, rh=50.0),
    ]
    offset = 3.0
    draws = b.fixed_draws(
        model, ["station_kit"], overrides={"rh_offset_percent": {"station_kit": np.array([offset])}}
    )
    result, trace, steps = _evaluate(model, segments, draws, zones, packages, settings)
    expected = 0.0
    for index, s in enumerate(segments):
        rh_out = s.relative_humidity_percent + offset
        k_out = kinetics.rate_constant(-17.0, 85000.0, 0.04, s.temperature_k, rh_out, T_REF, RH_REF)
        b_prime = trace.rh_in_start_high[index] + offset * steps["station_kit"][index] - rh_out
        expected += k_out * kinetics.humidity_integral(0.04 * b_prime, 86400.0, trace.time_constant_s[index])
    assert result.theta[0, 0] == pytest.approx(expected, rel=1e-12)


def test_negative_b_uses_the_low_gap_variant_everywhere(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = b.thermal_model(ln_k=-17.0, ea=85000.0, b=-0.02, with_radiolysis=False)
    segments = [
        b.segment(0.0, 3600.0, package="original_vial", rh=70.0, rh_low=20.0, gaps=("relative_humidity",)),
        b.segment(3600.0, 7200.0, package="original_vial", rh=40.0),
    ]
    offset = 5.0
    draws = b.fixed_draws(
        model, ["station_kit"], overrides={"rh_offset_percent": {"station_kit": np.array([offset])}}
    )
    result, _, _ = _evaluate(model, segments, draws, zones, packages, settings)
    # Vial: RH_in = RH_out. The gap segment takes the low bound and no offset; the other
    # segment takes the offset.
    k1 = kinetics.rate_constant(-17.0, 85000.0, -0.02, 298.15, 20.0, T_REF, RH_REF) * 3600.0
    k2 = kinetics.rate_constant(-17.0, 85000.0, -0.02, 298.15, 40.0 + offset, T_REF, RH_REF) * 3600.0
    assert result.theta[0, 0] == pytest.approx(k1 + k2, rel=1e-12)


def test_zone_multiplier_temperature_offset_and_gap_mask(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = b.thermal_model(ln_k=-17.0, ea=85000.0, b=None, with_radiolysis=False)
    segments = [
        b.segment(0.0, 3600.0, temperature=300.0),
        b.segment(3600.0, 7200.0, temperature=300.0, gaps=("temperature_k",)),
    ]
    draws = b.fixed_draws(
        model,
        ["station_kit"],
        overrides={
            "zone_ln_multiplier": {"station_kit": np.array([0.25])},
            "temperature_offset_k": {"station_kit": np.array([2.0])},
        },
    )
    result, _, _ = _evaluate(model, segments, draws, zones, packages, settings)
    k_hot = kinetics.rate_constant(-17.0, 85000.0, 0.0, 302.0, 50.0, T_REF, RH_REF)
    k_gap = kinetics.rate_constant(-17.0, 85000.0, 0.0, 300.0, 50.0, T_REF, RH_REF)
    assert result.theta[0, 0] == pytest.approx(math.exp(0.25) * 3600.0 * (k_hot + k_gap), rel=1e-12)


def test_widening_outside_the_validated_range(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(ln_k=-17.0, ea=85000.0, b=0.03, with_radiolysis=False)
    segments = [b.segment(0.0, 3600.0, temperature=320.0, package="original_vial", rh=90.0)]
    z_t, z_rh = 1.5, -0.5
    draws = b.fixed_draws(
        model,
        ["station_kit"],
        overrides={
            "extrapolation_z_temperature": np.array([[z_t]]),
            "extrapolation_z_humidity": np.array([[z_rh]]),
        },
    )
    result, _, _ = _evaluate(model, segments, draws, zones, packages, settings)
    extrapolation = settings["extrapolation"]
    widening = z_t * extrapolation["ln_k_sd_per_kelvin"]["value"] * (320.0 - 313.15) + z_rh * extrapolation[
        "ln_k_sd_per_percent_rh"
    ]["value"] * (90.0 - 75.0)
    base = kinetics.rate_constant(-17.0, 85000.0, 0.03, 320.0, 90.0, T_REF, RH_REF) * 3600.0
    assert result.theta[0, 0] == pytest.approx(base * math.exp(widening), rel=1e-12)


def _light_model() -> Any:
    light = b.pathway(
        "api:photolysis",
        b.block(["ln_k_vis_per_lux_h", "ln_k_uv_per_w_h_per_m2"], [-16.0, -9.0], [1.0, 1.0]),
        driver="light",
        mechanism="photolysis",
        validated=b.WIDE_RANGE,
    )
    vis_only = b.pathway(
        "api:photolysis_vis",
        b.block(["ln_k_vis_per_lux_h"], [-15.0], [1.0]),
        driver="light",
        mechanism="photolysis",
        validated=b.WIDE_RANGE,
    )
    return b.model([light, vis_only])


def test_light_pathways_need_light_exposure(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = _light_model()
    lit = [b.segment(0.0, 7200.0, zone="mars_base_pharmacy", package="repackaged_bag", lux=500.0, uv=2.0)]
    dark = [b.segment(0.0, 7200.0, zone="station_kit", package="repackaged_bag", lux=500.0, uv=2.0)]
    draws = b.fixed_draws(model, ["mars_base_pharmacy", "station_kit"])
    result, _, _ = _evaluate(model, lit, draws, zones, packages, settings)
    bag = packages["repackaged_bag"]
    tv = bag["light_transmission_visible"]["value"]
    tu = bag["light_transmission_uv"]["value"]
    both = (math.exp(-16.0) * tv * 500.0 + math.exp(-9.0) * tu * 2.0) * 2.0
    assert result.theta[0, 0] == pytest.approx(both, rel=1e-13)
    assert result.theta[0, 1] == pytest.approx(math.exp(-15.0) * tv * 500.0 * 2.0, rel=1e-13)
    none, _, _ = _evaluate(model, dark, draws, zones, packages, settings)
    assert none.theta[0].tolist() == [0.0, 0.0]


def _solution_model(*, racemizing: bool = True) -> Any:
    paths = [
        b.pathway(
            "epi:catechol_autoxidation",
            b.block(
                ["ln_k_ref_per_s", "ea_j_per_mol", "ln_frozen_rate_multiplier"],
                [-17.0, 65000.0, 1.5],
                [0.6, 1e4, 1.0],
            ),
            mechanism="autoxidation",
            degradant_spec=b.degradant("catechol_autoxidation", yield_mol=0.5),
        ),
        b.pathway(
            "epi:radiolysis",
            b.block(["ln_k_dose_per_gy", "ln_k_dose_frozen_per_gy"], [-8.0, -11.0], [0.5, 1.0]),
            driver="radiation",
            mechanism="radiolysis",
            validated=b.WIDE_RANGE,
        ),
    ]
    if racemizing:
        paths.append(
            b.pathway(
                "epi:racemization",
                b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-16.5, 90000.0], [0.7, 1e4]),
                role="racemization",
                mechanism="racemization",
                validated=b.WIDE_RANGE,
            )
        )
    return b.model(
        paths,
        product_id="epi",
        ingredient_id="epi",
        family="solution",
        f0=0.995 if racemizing else 1.0,
        freezing=272.65,
    )


def test_frozen_solution_racemization_and_radiolysis(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = _solution_model()
    segments = [
        b.segment(0.0, 3600.0, package="original_vial", temperature=270.0, dose=1e-6),
        b.segment(3600.0, 7200.0, package="original_vial", temperature=300.0, dose=1e-6),
    ]
    on = copy.deepcopy(settings)
    on["radiation"]["include_in_potency"] = True
    draws = b.fixed_draws(model, ["station_kit"])
    result, _, _ = _evaluate(model, segments, draws, zones, packages, on)
    ids = [p.pathway_id for p in model.pathways]
    auto, rad, race = (
        ids.index("epi:catechol_autoxidation"),
        ids.index("epi:radiolysis"),
        ids.index("epi:racemization"),
    )
    k_frozen = kinetics.rate_constant(-17.0, 65000.0, 0.0, 270.0, 50.0, T_REF, RH_REF) * math.exp(1.5)
    k_warm = kinetics.rate_constant(-17.0, 65000.0, 0.0, 300.0, 50.0, T_REF, RH_REF)
    assert result.theta[0, auto] == pytest.approx(3600.0 * (k_frozen + k_warm), rel=1e-12)
    assert result.theta[0, rad] == pytest.approx(
        3600.0 * 1e-6 * (math.exp(-11.0) + math.exp(-8.0)), rel=1e-12
    )
    theta_r = 3600.0 * (
        kinetics.rate_constant(-16.5, 90000.0, 0.0, 270.0, 50.0, T_REF, RH_REF)
        + kinetics.rate_constant(-16.5, 90000.0, 0.0, 300.0, 50.0, T_REF, RH_REF)
    )
    assert result.theta[0, race] == pytest.approx(theta_r, rel=1e-12)
    rho = 0.5 * (1.0 + (2 * 0.995 - 1.0) * math.exp(-2.0 * theta_r))
    assert result.active_fraction[0] == pytest.approx(rho, rel=1e-14)
    assert result.potency_active_percent[0] == pytest.approx(result.potency_total_percent[0] * rho, rel=1e-14)
    destructive = result.theta[0, auto] + result.theta[0, rad]
    assert result.alpha[0] == pytest.approx(-math.expm1(-destructive), rel=1e-12)
    # Exact attribution: the autoxidation share of the conversion, times its yield.
    assert 0.0 < result.degradant_percent[0, auto] < 100.0 * 0.5 * result.alpha[0]


def test_degradant_attribution_sums_to_the_conversion(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    first = b.pathway(
        "api:a",
        b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-14.0, 80000.0], [0.3, 1e4]),
        degradant_spec=b.degradant("a"),
    )
    second = b.pathway(
        "api:b",
        b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-14.5, 120000.0], [0.3, 1e4]),
        degradant_spec=b.degradant("b"),
    )
    model = b.model([first, second], kind="F2")
    segments = [b.segment(i * 86400.0, (i + 1) * 86400.0, temperature=290.0 + 5 * i) for i in range(5)]
    draws = b.fixed_draws(model, ["station_kit"])
    result, _, _ = _evaluate(model, segments, draws, zones, packages, settings)
    assert float(np.sum(result.degradant_percent[0])) == pytest.approx(100.0 * result.alpha[0], rel=1e-12)


def test_projected_and_partial_thetas_agree_with_the_grid(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = _solution_model()
    rng = np.random.default_rng(5)
    segments = [
        b.segment(
            i * 3600.0,
            (i + 1) * 3600.0,
            package="repackaged_bag",
            temperature=float(285 + 20 * rng.random()),
            rh=float(30 + 40 * rng.random()),
            dose=1e-7,
        )
        for i in range(300)
    ]
    count = len(model.pathways)
    draws = b.fixed_draws(
        model,
        ["station_kit"],
        n=4,
        overrides={
            "ln_k_ref_per_s": np.tile([[-17.0, 0.0, -16.5]], (4, 1)) + rng.normal(0, 0.3, (4, count)),
            "rh_offset_percent": {"station_kit": rng.normal(0, 2, 4)},
            "temperature_offset_k": {"station_kit": rng.normal(0, 0.5, 4)},
        },
    )
    exposure = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(exposure.segments, packages)
    steps = _steps(exposure.segments, trace, ["station_kit"], tracks=True)
    result = kinetics.evaluate_draws(model, exposure, trace, steps, draws, zones, packages, settings)
    per_d, per_r = kinetics.projected_thetas(model, exposure, trace, steps, draws, zones, packages, settings)
    sum_d, sum_r, _ = kinetics.pathway_sums(model, result.theta, settings)
    assert np.allclose(per_d.sum(axis=0), sum_d, rtol=1e-12, atol=0.0)
    assert np.allclose(per_r.sum(axis=0), sum_r, rtol=1e-12, atol=0.0)
    index = np.array([0, 17, 299, 150], dtype=np.intp)
    partial = kinetics.partial_segment(model, exposure, trace, steps, draws, zones, packages, settings, index)
    full_d, full_r = kinetics.partial_thetas(partial, np.full(4, 3600.0))
    assert np.allclose(full_d, per_d[index, np.arange(4)], rtol=1e-13, atol=0.0)
    assert np.allclose(full_r, per_r[index, np.arange(4)], rtol=1e-13, atol=0.0)
    half_d, _ = kinetics.partial_thetas(partial, np.full(4, 1800.0))
    assert bool(np.all(half_d < full_d))
    assert bool(np.all(half_d > 0.0))
    zero_d, zero_r = kinetics.partial_thetas(partial, np.zeros(4))
    assert zero_d.tolist() == [0.0] * 4
    assert zero_r.tolist() == [0.0] * 4
    picked = partial.select(np.array([2, 0], dtype=np.intp))
    joined = kinetics.concatenate_partials([picked, partial.select(np.array([1], dtype=np.intp))])
    again_d, _ = kinetics.partial_thetas(joined, np.full(3, 3600.0))
    assert np.allclose(again_d, full_d[[2, 0, 1]], rtol=1e-15, atol=0.0)
    sliced = kinetics.slice_draws(draws, np.array([3, 1], dtype=np.intp))
    sub = kinetics.evaluate_draws(model, exposure, trace, steps, sliced, zones, packages, settings)
    assert np.allclose(sub.theta, result.theta[[3, 1]], rtol=1e-13, atol=0.0)
    _, total, active = kinetics.potency_from_thetas(model, draws.c0_percent, sum_d, sum_r, settings)
    assert np.allclose(total, result.potency_total_percent, rtol=1e-13, atol=0.0)
    assert np.allclose(active, result.potency_active_percent, rtol=1e-13, atol=0.0)


def test_partial_segment_errors(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(with_radiolysis=False)
    exposure = b.lot_exposure([b.segment(0.0, 3600.0)])
    trace = microclimate.internal_rh_trace(exposure.segments, packages)
    steps = _steps(exposure.segments, trace, ["station_kit"], tracks=False)
    draws = b.fixed_draws(model, ["station_kit"], n=2)
    with pytest.raises(InputError):
        kinetics.partial_segment(
            model, exposure, trace, steps, draws, zones, packages, settings, np.array([0])
        )
    with pytest.raises(InputError):
        kinetics.partial_segment(
            model, exposure, trace, steps, draws, zones, packages, settings, np.array([0, 1])
        )
    with pytest.raises(InputError):
        kinetics.concatenate_partials([])
    other = _solution_model()
    other_exposure = b.lot_exposure([b.segment(0.0, 3600.0, package="original_vial")])
    other_trace = microclimate.internal_rh_trace(other_exposure.segments, packages)
    first = kinetics.partial_segment(
        model, exposure, trace, steps, draws, zones, packages, settings, np.array([0, 0])
    )
    second = kinetics.partial_segment(
        other,
        other_exposure,
        other_trace,
        _steps(other_exposure.segments, other_trace, ["station_kit"], False),
        b.fixed_draws(other, ["station_kit"], n=2),
        zones,
        packages,
        settings,
        np.array([0, 0]),
    )
    with pytest.raises(InputError):
        kinetics.concatenate_partials([first, second])
    with pytest.raises(InputError):
        kinetics.pathway_sums(model, np.zeros((2, 5)), settings)


def test_evaluate_draws_input_errors(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(with_radiolysis=False)
    segments = [b.segment(0.0, 3600.0)]
    exposure = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(exposure.segments, packages)
    steps = _steps(exposure.segments, trace, ["station_kit"], tracks=False)
    good = b.fixed_draws(model, ["station_kit"])

    def run(**changes: Any) -> None:
        args: dict[str, Any] = {"exposure": exposure, "trace": trace, "steps": steps, "draws": good}
        args.update(changes)
        kinetics.evaluate_draws(
            model, args["exposure"], args["trace"], args["steps"], args["draws"], zones, packages, settings
        )

    with pytest.raises(InputError):
        run(draws=b.fixed_draws(model, ["station_kit"], n=0))
    with pytest.raises(InputError):
        run(draws=b.fixed_draws(model, ["station_kit"], overrides={"c0_percent": np.ones(3)}))
    with pytest.raises(InputError):
        run(draws=b.fixed_draws(model, ["station_kit"], overrides={"ea_j_per_mol": np.ones((1, 3))}))
    with pytest.raises(InputError):
        run(draws=b.fixed_draws(model, ["moon_base_pharmacy"]))
    with pytest.raises(InputError):
        run(
            draws=b.fixed_draws(
                model, ["station_kit"], overrides={"zone_ln_multiplier": {"station_kit": np.ones(2)}}
            )
        )
    with pytest.raises(InputError):
        run(steps={})
    with pytest.raises(InputError):
        run(steps={"station_kit": (0.0, 0.0)})
    with pytest.raises(InputError):
        run(steps={"station_kit": (0.0,), "nowhere": (0.0,)})
    with pytest.raises(InputError):
        run(trace=microclimate.internal_rh_trace((), packages))
    with pytest.raises(InputError):
        run(exposure=b.lot_exposure([b.segment(3600.0, 0.0)]))
    with pytest.raises(ModelDomainError):
        run(exposure=b.lot_exposure([b.segment(0.0, 3600.0, package="nope")]))
    with pytest.raises(ComputationError):
        run(
            draws=b.fixed_draws(
                model,
                ["station_kit"],
                overrides={"temperature_offset_k": {"station_kit": np.array([-400.0])}},
            )
        )
    with pytest.raises(ComputationError):
        run(draws=b.fixed_draws(model, ["station_kit"], overrides={"ln_k_ref_per_s": np.array([[800.0]])}))


def test_pathways_missing_their_rate_parameter(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    segments = [b.segment(0.0, 3600.0)]
    for path in (
        b.pathway("api:t", b.block(["ea_j_per_mol"], [80000.0], [1e4])),
        b.pathway("api:l", b.block(["ea_j_per_mol"], [80000.0], [1e4]), driver="light"),
        b.pathway("api:r", b.block(["ea_j_per_mol"], [80000.0], [1e4]), driver="radiation"),
    ):
        model = b.model([path])
        with pytest.raises(ModelDomainError):
            _evaluate(model, segments, b.fixed_draws(model, ["station_kit"]), zones, packages, settings)


def test_empty_history_gives_the_initial_content(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = b.thermal_model()
    draws = b.fixed_draws(model, ["station_kit"], c0=98.0)
    exposure = b.lot_exposure([])
    trace = microclimate.internal_rh_trace((), packages)
    result = kinetics.evaluate_draws(
        model, exposure, trace, {"station_kit": ()}, draws, zones, packages, settings
    )
    assert result.potency_total_percent.tolist() == [98.0]
    assert result.theta.tolist() == [[0.0, 0.0]]


# =========================================================================================
# Nominal draw, shares, regimes and placards (spec 5.8, 8, 9.6)
# =========================================================================================


def test_ln_rate_multiplier_families() -> None:
    z = np.array([0.0, 1.0])
    assert kinetics.ln_rate_multiplier("lognormal", 0.1, 0.2, z).tolist() == [0.1, pytest.approx(0.3)]
    assert kinetics.ln_rate_multiplier("fixed", 1.0, 0.0, z).tolist() == [0.0, 0.0]
    with pytest.raises(ModelDomainError):
        kinetics.ln_rate_multiplier("normal", 0.0, 1.0, z)
    with pytest.raises(ModelDomainError):
        kinetics.ln_rate_multiplier("fixed", 0.0, 0.0, z)


def test_nominal_draw_uses_block_means(zones: Any) -> None:
    model = _solution_model()
    draw = kinetics.nominal_draw(model, zones, ["station_kit", "ground_storage"])
    assert draw.n == 1
    assert draw.c0_percent.tolist() == [100.0]
    ids = [p.pathway_id for p in model.pathways]
    assert draw.ln_k_dose_frozen_per_gy[0, ids.index("epi:radiolysis")] == -11.0
    assert draw.ln_frozen_multiplier[0, ids.index("epi:catechol_autoxidation")] == 1.5
    assert draw.zone_ln_multiplier["ground_storage"].tolist() == [0.0]
    assert sorted(draw.temperature_offset_k) == ["ground_storage", "station_kit"]
    with pytest.raises(InputError):
        kinetics.nominal_draw(model, zones, ["nowhere"])


def _nominal(
    model: Any,
    segments: list[Segment],
    zones: Any,
    packages: Any,
    settings: Any,
    histories: tuple[ZoneHistory, ...] = (),
) -> tuple[LotExposure, Any]:
    exposure = LotExposure("lot-test", segments[0].t0_s, segments[-1].t1_s, tuple(segments), histories, False)
    trace = microclimate.internal_rh_trace(exposure.segments, packages)
    return exposure, kinetics.nominal_thetas(model, exposure, trace, zones, packages, settings)


def test_nominal_thetas_and_shares(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = _solution_model()
    segments = [b.segment(0.0, 86400.0, package="original_vial", temperature=300.0, dose=1e-6)]
    _exposure, nominal = _nominal(model, segments, zones, packages, settings)
    by_id = {n.pathway_id: n for n in nominal}
    k = kinetics.rate_constant(-17.0, 65000.0, 0.0, 300.0, 50.0, T_REF, RH_REF) * 86400.0
    prior = zones["station_kit"]["rate_multiplier"]
    k *= math.exp(prior["mu"]) if prior["family"] == "lognormal" else prior["mu"]
    assert by_id["epi:catechol_autoxidation"].theta == pytest.approx(k, rel=1e-12)
    assert by_id["epi:catechol_autoxidation"].segment_theta == (pytest.approx(k, rel=1e-12),)
    shares = kinetics.nominal_shares(model, nominal, settings)
    assert sum(shares) == pytest.approx(1.0, rel=1e-12)
    ids = [p.pathway_id for p in model.pathways]
    assert shares[ids.index("epi:radiolysis")] == 0.0  # radiolysis outside potency
    flags = kinetics.contributing_pathways(model, nominal, settings)
    assert flags[ids.index("epi:catechol_autoxidation")]
    with pytest.raises(InputError):
        kinetics.nominal_shares(model, nominal[:1], settings)
    # The radiolysis pathway sits outside potency, so it never contributes ...
    plain = b.thermal_model()
    _, warm = _nominal(plain, [b.segment(0.0, 86400.0)], zones, packages, settings)
    assert kinetics.contributing_pathways(plain, warm, settings) == (True, False)
    # ... unless nothing does (an empty history): then every pathway counts (spec 9.6).
    idle = tuple(dataclasses.replace(n, theta=0.0) for n in warm)
    assert kinetics.contributing_pathways(plain, idle, settings) == (True, True)


def test_inside_the_range_raises_nothing(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(b=0.03)
    exposure, nominal = _nominal(model, [b.segment(0.0, 86400.0)], zones, packages, settings)
    found = kinetics.assess_validated_range(model, exposure, nominal, settings)
    assert not found.outside_validated_range
    assert not found.regime_switch
    assert found.placards == ()
    assert found.share_outside == 0.0
    assert found.time_extrapolation_ratio is None


def test_temperature_and_humidity_placards(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(b=0.03)
    segments = [
        b.segment(0.0, 86400.0, package="original_vial", temperature=300.0, rh=50.0),
        b.segment(86400.0, 2 * 86400.0, package="original_vial", temperature=320.0, rh=80.0),
    ]
    exposure, nominal = _nominal(model, segments, zones, packages, settings)
    found = kinetics.assess_validated_range(model, exposure, nominal, settings)
    kinds = [p.kind for p in found.placards]
    assert kinds == ["temperature_range", "humidity_range"]
    temperature, humidity = found.placards
    assert temperature.extent == pytest.approx(320.0 - 313.15)
    assert temperature.unit == "K"
    assert temperature.duration_s == 86400.0
    assert humidity.extent == pytest.approx(5.0)
    assert humidity.unit == "%RH"
    assert 0.5 < temperature.share_nominal <= 1.0
    assert found.outside_validated_range
    assert not found.regime_switch
    assert kinetics.placards_for(model, exposure, nominal, settings) == found.placards


def test_time_extrapolation_placard(zones: Any, packages: Any, settings: EngineSettingsDoc) -> None:
    model = b.thermal_model(b=None)
    measured = dataclasses.replace(model, measured_model_id="m1", max_study_duration_s=86400.0)
    segments = [b.segment(i * 86400.0, (i + 1) * 86400.0) for i in range(5)]
    exposure, nominal = _nominal(measured, segments, zones, packages, settings)
    found = kinetics.assess_validated_range(measured, exposure, nominal, settings)
    assert found.time_extrapolation_ratio == pytest.approx(5.0)
    assert found.time_extrapolated
    (placard,) = [p for p in found.placards if p.kind == "time_extrapolation"]
    assert placard.extent == pytest.approx(5.0)
    assert placard.unit == "1"
    assert placard.duration_s == pytest.approx(3 * 86400.0)
    assert placard.share_nominal == pytest.approx(0.6, rel=1e-9)
    assert found.outside_validated_range
    with pytest.raises(ModelDomainError):
        kinetics.assess_validated_range(
            dataclasses.replace(measured, max_study_duration_s=0.0), exposure, nominal, settings
        )


def test_frozen_phase_change_and_deliquescence(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    solution = _solution_model(racemizing=False)
    segments = [
        b.segment(0.0, 3600.0, package="original_vial", temperature=270.0),
        b.segment(3600.0, 7200.0, package="original_vial", temperature=300.0),
    ]
    exposure, nominal = _nominal(solution, segments, zones, packages, settings)
    found = kinetics.assess_validated_range(solution, exposure, nominal, settings)
    assert found.frozen
    assert found.regime_switch
    assert found.outside_validated_range
    (frozen,) = [p for p in found.placards if p.kind == "regime_frozen"]
    assert frozen.extent == pytest.approx(272.65 - 270.0)
    assert frozen.duration_s == 3600.0
    solid = dataclasses.replace(
        b.thermal_model(b=0.03), phase_change_temperature_k=305.0, deliquescence_rh_percent=70.0
    )
    segments = [
        b.segment(0.0, 3600.0, package="original_vial", temperature=307.0, rh=50.0),
        b.segment(3600.0, 7200.0, package="original_vial", temperature=300.0, rh=72.0),
    ]
    exposure, nominal = _nominal(solid, segments, zones, packages, settings)
    found = kinetics.assess_validated_range(solid, exposure, nominal, settings)
    assert found.phase_change
    assert found.deliquescence
    assert found.regime_switch
    kinds = [p.kind for p in found.placards]
    assert kinds == ["humidity_range", "regime_phase_change"]
    humidity = found.placards[0]
    assert humidity.extent == pytest.approx(2.0)
    assert found.placards[1].extent == pytest.approx(2.0)


def test_possibly_frozen_from_a_temperature_gap(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    solution = _solution_model(racemizing=False)
    gap_segment = b.segment(0.0, 3600.0, package="original_vial", temperature=300.0, gaps=("temperature_k",))
    record = GapRecord("station_kit", "temperature_k", 0.0, 3600.0, "worst_case", 300.0, 265.0)
    history = ZoneHistory("station_kit", 0.0, 3600.0, (gap_segment,), (record,), (), (), False)
    exposure, nominal = _nominal(solution, [gap_segment], zones, packages, settings, (history,))
    found = kinetics.assess_validated_range(solution, exposure, nominal, settings)
    assert found.possibly_frozen
    assert not found.frozen
    assert found.regime_switch
    (placard,) = [p for p in found.placards if p.kind == "regime_frozen"]
    assert placard.extent == pytest.approx(272.65 - 265.0)
    orphan = LotExposure("lot-test", 0.0, 3600.0, (gap_segment,), (), False)
    with pytest.raises(InputError):
        kinetics.assess_validated_range(solution, orphan, nominal, settings)


def test_range_assessment_needs_segment_detail(
    zones: Any, packages: Any, settings: EngineSettingsDoc
) -> None:
    model = b.thermal_model()
    exposure, nominal = _nominal(model, [b.segment(0.0, 3600.0)], zones, packages, settings)
    bare = tuple(dataclasses.replace(n, segment_theta=()) for n in nominal)
    with pytest.raises(InputError):
        kinetics.assess_validated_range(model, exposure, bare, settings)


def test_active_fraction_bounds() -> None:
    theta = np.array([0.0, 0.1, 1.0, 50.0])
    rho = kinetics.active_fraction(theta, 0.98)
    assert rho[0] == pytest.approx(0.98)
    assert rho[-1] == pytest.approx(0.5)
    assert bool(np.all(np.diff(rho) <= 0.0))


def test_has_humidity_term() -> None:
    with_b = b.thermal_model(b=0.02).pathways[0]
    without = b.thermal_model(b=None).pathways[0]
    fixed_zero = b.pathway("api:z", b.block(["ln_k_ref_per_s", "b_per_percent_rh"], [-17.0, 0.0], [0.3, 0.0]))
    assert kinetics.has_humidity_term(with_b)
    assert not kinetics.has_humidity_term(without)
    assert not kinetics.has_humidity_term(fixed_zero)


def test_seconds_per_day_constant_is_used_consistently() -> None:
    assert SECONDS_PER_DAY == 86400.0

"""Self-tests of the oracle: analytic identities, independent mpmath routes (quadrature,
numerical differentiation, the Taylor ODE solver) and the full-precision vector values.

These never touch engine code. They must pass before any oracle agreement test means
anything.
"""

import hashlib
import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Any

import pytest

from pharmacy.types import PackageDoc, RateLawKind, Segment
from tests.oracle import chemistry, exact, kinetics, microclimate, targets, truth
from tests.oracle.exposure import arithmetic_mean_temperature, mean_kinetic_temperature
from tests.oracle.precision import MP, R_EXACT, R_MP, agrees, mpf

TIGHT = MP.mpf(10) ** -40
KIND_PARAMS: dict[RateLawKind, dict[str, float]] = {
    "Fn": {"order_n": 1.7},
    "An": {"avrami_n": 1.5},
    "B1": {"alpha0": 0.01},
}
FN_BELOW_ONE: dict[str, float] = {"order_n": 0.5}


def _params(kind: RateLawKind) -> dict[str, float]:
    return KIND_PARAMS.get(kind, {})


def _close(a: Any, b: Any, tol: Any = TIGHT) -> bool:
    return bool(abs(MP.mpf(a) - MP.mpf(b)) <= tol * max(1, abs(MP.mpf(b))))


# =========================================================================================
# Constants
# =========================================================================================


def test_gas_constant_is_the_exact_si_product() -> None:
    assert Fraction("8.31446261815324") == R_EXACT
    assert MP.mpf("8.31446261815324") == R_MP
    assert float(R_MP) == 8.31446261815324


# =========================================================================================
# Kinetics (spec 5.1 to 5.4)
# =========================================================================================


def test_rate_constant_at_reference_is_k_star() -> None:
    k = kinetics.rate_constant(-20.0, 1.0e5, 0.05, 298.15, 40.0, 298.15, 40.0)
    assert _close(k, MP.exp(-20))


def test_rate_constant_arrhenius_and_humidity_factors() -> None:
    k1 = kinetics.rate_constant(-20.0, 1.0e5, 0.05, 310.0, 60.0, 298.15, 40.0)
    expected = MP.exp(-20) * MP.exp(-MP.mpf(1.0e5) / R_MP * (1 / MP.mpf(310.0) - 1 / MP.mpf(298.15)))
    expected *= MP.exp(MP.mpf(0.05) * 20)
    assert _close(k1, expected)


@pytest.mark.parametrize("kind", kinetics.ALL_KINDS)
def test_g_of_capital_g_is_identity(kind: RateLawKind) -> None:
    params = _params(kind)
    complete = kinetics.completion_theta(kind, **params)
    top = min(MP.mpf(10), complete)
    if kind in ("A2", "A3", "A4", "An"):
        top = min(top, MP.mpf(50) ** (1 / MP.mpf({"A2": 2, "A3": 3, "A4": 4}.get(kind, 1.5))))
    # 1 - alpha reaches e^-50: evaluate at 200 digits so the round trip tests the formulas,
    # not the 50-digit representation of alpha near 1
    with MP.workdps(200):
        for i in range(1, 41):
            theta = top * i / 41
            alpha = kinetics.conversion_from_theta(kind, theta, **params)
            back = kinetics.theta_from_conversion(kind, alpha, **params)
            assert _close(back, theta, MP.mpf(10) ** -100), (kind, i)


def test_fn_below_one_completes_at_one_over_one_minus_n() -> None:
    assert kinetics.completion_theta("Fn", **FN_BELOW_ONE) == 2
    assert kinetics.conversion_from_theta("Fn", 2.0, **FN_BELOW_ONE) == 1
    assert kinetics.conversion_from_theta("Fn", 5.0, **FN_BELOW_ONE) == 1
    alpha = kinetics.conversion_from_theta("Fn", 1.0, **FN_BELOW_ONE)
    assert _close(kinetics.theta_from_conversion("Fn", alpha, **FN_BELOW_ONE), 1)


@pytest.mark.parametrize("kind", kinetics.ALL_KINDS)
def test_rate_function_is_reciprocal_of_g_derivative(kind: RateLawKind) -> None:
    params = _params(kind)
    for alpha in ("0.05", "0.2", "0.5", "0.8"):
        a = MP.mpf(alpha)
        if kind == "D4" and a > MP.mpf("0.99"):
            continue
        derivative = MP.diff(lambda x: kinetics.theta_from_conversion(kind, x, **params), a)
        assert _close(kinetics.rate_function(kind, a, **params) * derivative, 1, MP.mpf(10) ** -30), (
            kind,
            alpha,
        )


@pytest.mark.parametrize(
    "kind", ["F0", "F1", "F2", "Fn", "R2", "R3", "A2", "A3", "B1", "D1", "D2", "D3", "D4"]
)
def test_closed_forms_agree_with_mpmath_taylor_ode(kind: RateLawKind) -> None:
    """Integrate d(alpha)/dTheta = f(alpha) with mpmath's Taylor-series ODE solver. Kinds
    with f singular or zero at alpha = 0 start at alpha = 0.01 with the Theta offset g(0.01)
    (the engine's reference uses 1e-9, spec 5.9; any start checks the closed form)."""
    params = _params(kind)
    with MP.workdps(30):
        singular = kind in ("D1", "D2", "D3", "D4", "A2", "A3")
        start_alpha = MP.mpf("0.01") if singular else MP.mpf(0)
        offset = kinetics.theta_from_conversion(kind, start_alpha, **params) if singular else MP.mpf(0)
        solution = MP.odefun(lambda _t, y: kinetics.rate_function(kind, y, **params), offset, start_alpha)
        top = offset + min(
            MP.mpf("0.3"), (kinetics.completion_theta(kind, **params) - offset) * MP.mpf("0.9")
        )
        for theta in (offset + (top - offset) / 3, top):
            ode_alpha = solution(theta)
            closed = kinetics.conversion_from_theta(kind, theta, **params)
            assert abs(ode_alpha - closed) < MP.mpf("1e-15"), (kind, theta)


def test_beyond_completion_saturates_at_one() -> None:
    for kind in kinetics.FINITE_COMPLETION:
        complete = kinetics.completion_theta(kind)
        assert kinetics.conversion_from_theta(kind, complete) == 1
        assert kinetics.conversion_from_theta(kind, complete * 3) == 1


def test_large_theta_saturates_without_overflow() -> None:
    for kind in ("F1", "A2", "A4", "B1"):
        alpha = kinetics.conversion_from_theta(kind, 1e6, **_params(kind))
        assert 1 - alpha < MP.mpf(10) ** -40


@pytest.mark.parametrize("bad", [("Fn", {"order_n": 1.0}), ("Fn", {"order_n": -1.0}), ("An", {}), ("B1", {})])
def test_kind_parameter_domain_errors(bad: tuple[RateLawKind, dict[str, float]]) -> None:
    kind, params = bad
    with pytest.raises(kinetics.OracleDomainError):
        kinetics.conversion_from_theta(kind, 0.1, **params)


@pytest.mark.parametrize("x", [-50.0, -12.0, -5.0, -1.0, -1e-3, 1e-3, 0.3, 5.0, 20.0, 50.0])
@pytest.mark.parametrize("ratio", [1e-6, 1e-2, 1.0, 10.0, 1e3])
def test_humidity_integral_ei_form_matches_quadrature(x: float, ratio: float) -> None:
    tau = 3.7e5
    duration = ratio * tau
    closed = kinetics.humidity_integral(x, duration, tau)
    quad = kinetics.humidity_integral_quadrature(x, duration, tau)
    assert abs(closed - quad) <= MP.mpf(10) ** -25 * abs(quad), (x, ratio)
    lower = duration * MP.exp(min(x, 0.0))
    upper = duration * MP.exp(max(x, 0.0))
    assert lower <= closed <= upper


def test_humidity_integral_limits() -> None:
    assert kinetics.humidity_integral(0.7, 100.0, MP.inf) == 100 * MP.exp(MP.mpf(0.7))
    assert kinetics.humidity_integral(0.7, 100.0, 0.0) == 100
    assert kinetics.humidity_integral(0.0, 100.0, 50.0) == 100
    assert kinetics.humidity_integral(0.7, 0.0, 50.0) == 0
    # tau_m very large approaches the sealed limit, very small the open limit
    assert _close(kinetics.humidity_integral(0.7, 100.0, 1e30), 100 * MP.exp(MP.mpf(0.7)), MP.mpf(10) ** -25)
    assert _close(kinetics.humidity_integral(0.7, 100.0, 1e-30), 100, MP.mpf(10) ** -25)


def test_humidity_integral_series_form_of_the_spec() -> None:
    """J = D + tau sum_n x^n (1 - e^(-n D/tau)) / (n n!) (spec 5.4), summed at 50 digits."""
    for x, d, tau in ((-4.9, 3600.0, 7200.0), (2.5, 1e5, 3e4), (0.01, 10.0, 1e6)):
        xx, dd, tt = MP.mpf(x), MP.mpf(d), MP.mpf(tau)

        def term(n: Any, xx: Any = xx, dd: Any = dd, tt: Any = tt) -> Any:
            return xx**n * (-MP.expm1(-n * dd / tt)) / (n * MP.factorial(n))

        series = dd + tt * MP.nsum(term, [1, MP.inf])
        assert _close(kinetics.humidity_integral(x, d, tau), series, MP.mpf(10) ** -35)


def test_integrated_rate_vector_tv_int_1() -> None:
    k_ref = 1.1574074074074074e-09
    theta = kinetics.integrated_rate(
        math.log(k_ref), 83144.0, 0.0, [298.15, 313.15], [40.0, 40.0], [8640000.0, 864000.0], 298.15, 40.0
    )
    assert agrees(0.014985701370961854, theta, rel=1e-14)
    potency = 100 * (1 - kinetics.conversion_from_theta("F1", theta))
    assert agrees(98.5126025453952, potency, rel=1e-14)


# =========================================================================================
# MKT (spec 3.8)
# =========================================================================================


def test_mkt_foundation_values() -> None:
    t2 = [285.45, 289.04999999999995, 289.34999999999997, 287.84999999999997, 288.04999999999995]
    mkt = mean_kinetic_temperature(t2, [86400.0] * 5, 83144.0)
    assert agrees(14.902462131262894 + 273.15, mkt, rel=1e-14)
    mkt7 = mean_kinetic_temperature([298.15, 313.15], [8640000.0, 864000.0], 83144.0)
    assert agrees(27.774191277050704 + 273.15, mkt7, rel=1e-14)
    assert agrees(300.9241912770507, mkt7, rel=1e-14)


def test_mkt_constant_and_jensen() -> None:
    assert _close(mean_kinetic_temperature([300.0] * 4, [1.0, 2.0, 3.0, 4.0], 83144.0), 300)
    temps, durations = [280.0, 300.0, 320.0], [5.0, 1.0, 2.0]
    assert mean_kinetic_temperature(temps, durations, 83144.0) > arithmetic_mean_temperature(temps, durations)


def test_mkt_equal_intervals_reduce_to_usp_form() -> None:
    temps = [285.0, 291.5, 303.25, 299.0]
    h = MP.mpf(83144.0) / R_MP
    usp = h / (-MP.log(MP.fsum(MP.exp(-h / MP.mpf(t)) for t in temps) / len(temps)))
    assert _close(mean_kinetic_temperature(temps, [7.0] * 4, 83144.0), usp)


# =========================================================================================
# Microclimate (spec 4)
# =========================================================================================


def _bottle() -> PackageDoc:
    sv = _sourced
    return {
        "package_id": "bottle",
        "name": "bottle",
        "kind": "bottle",
        "description": "",
        "protective": True,
        "light_transmission_visible": sv(0.05),
        "light_transmission_uv": sv(0.01),
        "moisture_model": {
            "kind": "first_order_exchange",
            "sealed_rh_percent": None,
            "mvtr_mg_per_day": sv(1.3),
            "mvtr_test_temperature_k": sv(311.15),
            "mvtr_test_delta_rh_percent": sv(90.0),
            "permeation_activation_energy_j_per_mol": sv(40000.0),
            "headspace_capacity_mg_per_percent_rh": sv(0.0005),
            "contents": [
                {"name": "tablets", "dry_mass_g": sv(30.0), "sorption_slope_mg_per_g_per_percent_rh": sv(0.6)}
            ],
            "initial_internal_rh_percent": None,
        },
        "basis": "team_default",
        "source_ids": [],
        "note": "",
    }


def _sourced(value: float) -> Any:
    return {
        "value": value,
        "unit": "",
        "basis": "team_default",
        "source_ids": [],
        "to_be_set_by": None,
        "note": "",
    }


def _segment(
    t0: float, t1: float, zone: str, package: str, temp: float, rh: float, gaps: tuple[str, ...] = ()
) -> Segment:
    low = rh - 30.0 if "relative_humidity" in gaps else rh
    return Segment(t0, t1, zone, package, temp, rh, low, 0.0, 0.0, 0.0, gaps, ())  # type: ignore[arg-type]


def test_time_constant_at_test_temperature() -> None:
    bottle = _bottle()
    tau = microclimate.exchange_time_constant(bottle, 311.15)
    capacity = MP.mpf(30.0) * MP.mpf(0.6) + MP.mpf(0.0005)
    assert _close(tau, capacity / (MP.mpf(1.3) / 90 / 86400))


def test_relax_rh_limits_and_composition() -> None:
    assert microclimate.relax_rh(30.0, 70.0, 100.0, MP.inf) == 30
    assert microclimate.relax_rh(30.0, 70.0, 100.0, 0.0) == 70
    one = microclimate.relax_rh(microclimate.relax_rh(30.0, 70.0, 100.0, 500.0), 70.0, 200.0, 500.0)
    assert _close(one, microclimate.relax_rh(30.0, 70.0, 300.0, 500.0))


def _history() -> list[Segment]:
    segments = []
    t = 0.0
    for i in range(40):
        duration = 3600.0 * (1 + (i * 7) % 11)
        zone = "zone_a" if i < 25 else "zone_b"
        gaps: tuple[str, ...] = ("relative_humidity",) if i in (6, 7, 30) else ()
        segments.append(
            _segment(t, t + duration, zone, "bottle", 290.0 + (i * 3) % 17, 20.0 + (i * 13) % 60, gaps)
        )
        t += duration
    return segments


def test_unit_step_response_is_the_exact_offset_derivative() -> None:
    """Linearity of spec 4.4: shifting RH_out by d in zone z (non-gap segments) moves every
    RH_in start by d h_z, checked against a full recomputation of the trace."""
    packages: Mapping[str, PackageDoc] = {"bottle": _bottle()}
    base = _history()
    trace = microclimate.internal_rh_trace(base, packages)
    for zone in ("zone_a", "zone_b"):
        h = microclimate.unit_step_response(
            base, trace.time_constant_s, zone, first_start_tracks_outside=True
        )
        d = 3.25
        shifted = [
            _segment(
                s.t0_s,
                s.t1_s,
                s.zone_id,
                "bottle",
                s.temperature_k,
                s.relative_humidity_percent + d * microclimate.forcing_indicator(s, zone),
                s.gap_quantities,
            )
            for s in base
        ]
        moved = microclimate.internal_rh_trace(shifted, packages)
        for s in range(len(base)):
            expected = trace.rh_in_start_high[s] + d * h[s]
            assert _close(moved.rh_in_start_high[s], expected, MP.mpf(10) ** -35), (zone, s)
            assert 0 <= h[s] <= 1


# =========================================================================================
# Chemistry and parameters (spec 6, 7.3, 9.3 to 9.5)
# =========================================================================================


def test_radiation_constants() -> None:
    assert _close(chemistry.k_dose_direct(1e-7, 151.16), MP.mpf(1e-7) * MP.mpf(151.16) / 1000)
    assert _close(
        chemistry.k_dose_indirect(2e-7, 0.01, 3e-4), chemistry.k_dose_indirect_from_g_eff(2e-7, 0.01, 3e-4)
    )
    assert _close(
        chemistry.upper_bound_loss_fraction(1.5e-6, 10.0, 0.9), 1 - MP.exp(-MP.mpf(1.5e-6) * 10 * MP.mpf(0.9))
    )


def test_convert_reference_round_trip_and_covariance() -> None:
    names = ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"]
    mean = [-18.0, 90000.0, 0.03]
    cov = [[0.25, 300.0, 0.001], [300.0, 4.0e6, 0.0], [0.001, 0.0, 1e-4]]
    m1, c1 = chemistry.convert_reference(names, mean, cov, 313.15, 75.0, 298.15, 40.0)
    expected = MP.mpf(-18.0) - MP.mpf(90000.0) * (1 / MP.mpf(298.15) - 1 / MP.mpf(313.15)) / R_MP
    expected += MP.mpf(0.03) * (40 - 75)
    assert _close(m1[0], expected)
    back_mean, back_cov = chemistry.convert_reference(
        names, [float(v) for v in m1], [[float(v) for v in row] for row in c1], 298.15, 40.0, 313.15, 75.0
    )
    for i in range(3):
        assert _close(back_mean[i], mean[i], MP.mpf(10) ** -14)
        for j in range(3):
            assert abs(back_cov[i][j] - cov[i][j]) <= MP.mpf(10) ** -12 * (1 + abs(MP.mpf(cov[i][j])))


def test_precision_weighting_and_class_prior() -> None:
    mu, s = chemistry.precision_weighted(-20.0, 1.0, -18.0, 1.0)
    assert _close(mu, -19)
    assert _close(s, MP.sqrt(MP.mpf("0.5")))
    mu_p, s_p = chemistry.class_prior_ln_k(-20.3811, 0.545129, 3)
    assert _close(mu_p, MP.mpf(-20.3811) - MP.log(3))
    assert s_p == MP.mpf(0.545129)


def test_lfer_identities() -> None:
    assert _close(chemistry.hammett_delta_ln_k(2.494, 0.78, 0.0), MP.log(10) * MP.mpf(2.494) * MP.mpf(0.78))
    assert chemistry.taft_delta_ln_k(1.0, 1.0, 0.0, 0.0) == 0
    ep = chemistry.evans_polanyi_delta_ln_k(0.5, 360.0, 370.0, 298.15)
    assert _close(ep, MP.mpf(0.5) * 10 * 1000 / (R_MP * MP.mpf(298.15)))
    assert _close(chemistry.combine_sites([0.7], 1), MP.mpf(0.7))
    assert _close(chemistry.combine_sites([0.0, 0.0], 1), MP.log(2))


# =========================================================================================
# Exact functions and oracle-only targets
# =========================================================================================


def test_jcgm_vectors() -> None:
    assert exact.jcgm_batch_size(0.95) == 10_000
    assert exact.jcgm_batch_size(0.90) == 10_000
    assert exact.jcgm_batch_size(0.9995) == 200_000


def test_numerical_tolerance_examples() -> None:
    assert exact.numerical_tolerance(0.0124, 2) == 0.0005
    assert exact.numerical_tolerance(0.0996, 2) == 0.005
    assert exact.numerical_tolerance(1.0, 2) == 0.05
    assert exact.numerical_tolerance(0.0, 2) == 0.0
    assert exact.numerical_tolerance(1234.0, 2) == 50.0


def test_weighted_quantile_matches_definition() -> None:
    values = [5.0, 1.0, 3.0, 3.0, 2.0]
    weights = [0.1, 0.2, 0.3, 0.2, 0.2]
    assert exact.weighted_quantile(values, weights, 0.2) == 1.0
    assert exact.weighted_quantile(values, weights, 0.4) == 2.0
    assert exact.weighted_quantile(values, weights, 0.41) == 3.0
    assert exact.weighted_quantile(values, weights, 0.9) == 3.0
    assert exact.weighted_quantile(values, weights, 0.91) == 5.0
    assert exact.weighted_quantile(values, None, 0.6) == 3.0


def test_q1e_and_q3b_vectors() -> None:
    assert exact.q1e_max_shelf_life_months("no_significant_change_accelerated_little_variability", 12) == 24
    assert exact.q1e_max_shelf_life_months("no_significant_change_accelerated_little_variability", 18) == 30
    assert exact.q1e_max_shelf_life_months("refrigerated_with_statistics", 12) == 18
    assert (
        exact.q1e_max_shelf_life_months("significant_change_accelerated_none_intermediate_no_statistics", 12)
        == 15
    )
    thresholds = exact.q3b_thresholds(1500.0)
    assert thresholds["reporting_percent"] == Fraction(5, 100)
    assert thresholds["identification_percent"] == Fraction(2, 15)
    assert thresholds["qualification_percent"] == Fraction(1, 5)
    assert exact.q3b_exceeds(3.4, 3.0) is False
    assert exact.q3b_exceeds(3.5, 3.0) is True


def test_seed_derivation_restates_canonical_json() -> None:
    material = {"b": [1, 2.5, "x"], "a": {"z": None, "y": True}}
    digest = hashlib.sha256(b'{"a":{"y":true,"z":null},"b":[1,2.5,"x"]}').digest()
    assert exact.derive_seed(material) == int.from_bytes(digest[:16], "big")
    assert len(exact.seed_hex(exact.derive_seed(material))) == 32


def test_targets_foundation_values() -> None:
    assert agrees(10000.0, targets.delta_h_over_r(mpf("83.144"), mpf("0.0083144")), rel=1e-40)
    assert agrees(1900.0, targets.coverage_simulation_count(mpf("0.95"), mpf("0.005")), rel=1e-40)
    assert agrees(218.6584165804244, targets.first_order_half_life(0.00317), rel=1e-14)
    assert agrees(145.00987041003037, targets.first_order_half_life(-0.00478), rel=1e-14)
    assert [float(targets.water_loss_ratio(a, b)) for a, b in ((60, 25), (60, 40), (65, 35), (75, 25))] == [
        1.875,
        1.5,
        1.8571428571428572,
        3.0,
    ]
    g = targets.gci(6.063, 5.972, 5.863, 1.5, 1.333)
    assert agrees(1.537168595116931, g["p"], rel=1e-13)
    assert agrees(2.1689073917960777, g["gci_fine21_percent"], rel=1e-13)


# =========================================================================================
# Truth integrator (spec 15.8)
# =========================================================================================


def _truth_model() -> truth.TruthModel:
    return truth.TruthModel((truth.TruthPathway(-20.0, 100000.0, 0.04),), 298.15, 40.0)


def test_truth_constant_conditions_are_exact() -> None:
    model = _truth_model()
    times = [i * 1800.0 for i in range(50)]
    result = truth.integrate_truth(model, times, [303.15] * 50, [55.0] * 50, truth.PackageModel.open_dish())
    k = kinetics.rate_constant(-20.0, 100000.0, 0.04, 303.15, 55.0, 298.15, 40.0)
    assert agrees(result.theta_at[-1], k * times[-1], rel=1e-13)
    assert result.halving_relative_error < 1e-13


def test_truth_package_path_matches_humidity_integral() -> None:
    """Constant T and RH_out in a first-order package: Theta = k_T e^(B(RH_out - RH*)) J."""
    model = _truth_model()
    package = truth.PackageModel.from_doc(_bottle())
    times = [i * 1800.0 for i in range(200)]
    start = 20.0
    result = truth.integrate_truth(
        model, times, [303.15] * 200, [70.0] * 200, package, rh_in_start=start, relative_target=1e-9
    )
    tau = microclimate.exchange_time_constant(_bottle(), 303.15)
    k_out = kinetics.rate_constant(-20.0, 100000.0, 0.04, 303.15, 70.0, 298.15, 40.0)
    exact_theta = k_out * kinetics.humidity_integral(0.04 * (start - 70.0), times[-1], tau)
    assert agrees(result.theta_at[-1], exact_theta, rel=1e-8)


def test_truth_float_path_matches_mpmath_path() -> None:
    model = _truth_model()
    times = [i * 1800.0 for i in range(60)]
    temps = [298.15 + 2.0 * math.sin(i / 3.0) for i in range(60)]
    rhs = [40.0 + 15.0 * math.cos(i / 5.0) for i in range(60)]
    package = truth.PackageModel.from_doc(_bottle())
    result = truth.integrate_truth(model, times, temps, rhs, package)
    reference = truth.integrate_truth_mp(model, times, temps, rhs, package, substeps=result.substeps)
    assert agrees(result.theta_at[-1], reference, rel=1e-12)
    assert result.halving_relative_error < 1e-6


def test_truth_converges_to_the_integral_of_the_interpolant() -> None:
    """Open dish, linear truth between nodes: the refined trapezoid tends to the exact
    integral of k along the piecewise-linear interpolant (mpmath quadrature)."""
    model = _truth_model()
    times = [i * 1800.0 for i in range(9)]
    temps = [298.15, 299.0, 297.5, 300.0, 301.0, 299.0, 300.5, 298.0, 298.5]
    rhs = [40.0, 44.0, 38.0, 45.0, 41.0, 43.0, 39.0, 44.5, 40.0]
    result = truth.integrate_truth(
        model, times, temps, rhs, truth.PackageModel.open_dish(), relative_target=1e-7
    )
    assert result.halving_relative_error < 1e-7

    def k_at(i: int, s: Any) -> Any:
        temperature = MP.mpf(temps[i]) + (MP.mpf(temps[i + 1]) - MP.mpf(temps[i])) * s
        rh = MP.mpf(rhs[i]) + (MP.mpf(rhs[i + 1]) - MP.mpf(rhs[i])) * s
        return kinetics.rate_constant(-20.0, 100000.0, 0.04, temperature, rh, 298.15, 40.0)

    exact_theta = MP.fsum(1800 * MP.quad(lambda s, i=i: k_at(i, s), [0, 1]) for i in range(8))
    assert agrees(result.theta_at[-1], exact_theta, rel=1e-7)
    # the reported halving difference bounds the actual error (trapezoid: error ~ diff / 3)
    assert abs(MP.mpf(result.theta_at[-1]) - exact_theta) <= result.halving_relative_error * exact_theta

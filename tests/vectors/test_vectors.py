# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Every test vector of tests/data/vectors/vectors-*.json at its own tolerance (spec 15.1).

``reproduces``: assert within the vector's tolerance (and the full-precision foundation
values where the catalogue names them). ``known_mismatch``: assert the documented behaviour,
never the published number. ``unverified``: skip with the reason. The validation datasets
(validation-*.json, V1 to V13) are exercised in tests/validation.
"""

import functools
import inspect
import json
import math
from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pharmacy.config import ConfigBundle
from pharmacy.types import NumericsSettings, RateLawKind
from tests.oracle import exact, targets
from tests.oracle.precision import MP, R_FLOAT
from tests.validation.rules import SF2_DOCUMENTED_RANGES

VECTOR_DIR = Path(__file__).resolve().parents[1] / "data" / "vectors"
HYPOTHESIS = settings(derandomize=True, max_examples=200, deadline=None)
ZERO_C = 273.15
COVERED: set[str] = set()
type Vector = Callable[[str], dict[str, Any]]


def covers(*vector_ids: str) -> Callable[[Any], Any]:
    COVERED.update(vector_ids)
    return lambda f: f


def within(value: float, expected: float, tolerance: dict[str, Any] | None) -> bool:
    assert tolerance is not None
    if tolerance.get("abs") is not None:
        return abs(value - expected) <= float(tolerance["abs"]) * (1 + 1e-12)
    return abs(value - expected) <= float(tolerance["rel"]) * abs(expected) * (1 + 1e-12)


def rel_close(value: float, expected: float, rel: float) -> bool:
    return abs(value - expected) <= rel * abs(expected)


def holds_to_rounding(value: float, published: float) -> bool:
    """True when ``value`` rounds to ``published`` at the published number's last digit
    (within half a unit of it)."""
    exponent = Decimal(repr(published)).as_tuple().exponent
    assert isinstance(exponent, int)
    return abs(value - published) <= 0.5 * 10.0**exponent * (1 + 1e-9)


@functools.cache
def _numerics() -> NumericsSettings:
    from pharmacy.config import load_bundle

    return load_bundle().settings["numerics"]


# =========================================================================================
# MKT
# =========================================================================================


@covers("TV-MKT-1")
def test_tv_mkt_1(vector: Vector) -> None:
    v = vector("TV-MKT-1")
    value = targets.delta_h_over_r(
        MP.mpf(str(v["inputs"]["delta_h_kj_per_mol"])), MP.mpf(str(v["inputs"]["gas_constant_kj_per_mol_k"]))
    )
    assert within(float(value), v["expected"]["delta_h_over_r_k"], v["tolerance"])


@covers("TV-MKT-2", "TV-MKT-3", "TV-MKT-7")
@pytest.mark.parametrize(
    ("vector_id", "foundation_key"),
    [
        ("TV-MKT-2", "foundation_mkt_c"),
        ("TV-MKT-3", "foundation_mkt_c_with_273_1"),
        ("TV-MKT-7", "foundation_mkt_c"),
    ],
)
def test_tv_mkt_single(vector: Vector, vector_id: str, foundation_key: str) -> None:
    from pharmacy import exposure

    v = vector(vector_id)
    i = v["inputs"]
    mkt_k = exposure.mean_kinetic_temperature(
        i["temperatures_k"], i["durations_s"], i["activation_energy_j_per_mol"]
    )
    offset = i.get("kelvin_offset", ZERO_C)
    assert within(mkt_k - offset, v["expected"]["mkt_c"], v["tolerance"])
    full = v["our_recomputation"][foundation_key] + offset
    assert rel_close(mkt_k, full, 1e-9), (mkt_k, full)


@covers("TV-MKT-8")
def test_tv_mkt_8(vector: Vector) -> None:
    from pharmacy import exposure

    v = vector("TV-MKT-8")
    i = v["inputs"]
    for ea, key in zip(i["activation_energies_j_per_mol"], ("83144", "120000"), strict=True):
        mkt_k = exposure.mean_kinetic_temperature(i["temperatures_k"], i["durations_s"], ea)
        assert within(mkt_k - ZERO_C, v["expected"][f"mkt_c_at_{key}"], v["tolerance"])
        assert rel_close(mkt_k, v["our_recomputation"][f"foundation_mkt_c_at_{key}"] + ZERO_C, 1e-9)


SERIES = st.lists(st.tuples(st.floats(240.0, 350.0), st.floats(1.0, 1e7)), min_size=1, max_size=25)


@covers("TV-MKT-4")
@HYPOTHESIS
@given(t=st.floats(200.0, 400.0), durations=st.lists(st.floats(1e-3, 1e8), min_size=1, max_size=20))
def test_tv_mkt_4_constant_series(t: float, durations: list[float]) -> None:
    from pharmacy import exposure

    mkt = exposure.mean_kinetic_temperature([t] * len(durations), durations, 83144.0)
    assert rel_close(mkt, t, 1e-12)


@covers("TV-MKT-5")
@HYPOTHESIS
@given(series=SERIES)
def test_tv_mkt_5_not_below_arithmetic_mean(series: list[tuple[float, float]]) -> None:
    from pharmacy import exposure

    temps, durations = [s[0] for s in series], [s[1] for s in series]
    mkt = exposure.mean_kinetic_temperature(temps, durations, 83144.0)
    mean = math.fsum(t * d for t, d in series) / math.fsum(durations)
    assert mkt >= mean * (1 - 1e-12)


@covers("TV-MKT-6")
@HYPOTHESIS
@given(temps=st.lists(st.floats(240.0, 350.0), min_size=1, max_size=25), duration=st.floats(1e-3, 1e7))
def test_tv_mkt_6_equal_intervals_reduce_to_usp(temps: list[float], duration: float) -> None:
    from pharmacy import exposure

    mkt = exposure.mean_kinetic_temperature(temps, [duration] * len(temps), 83144.0)
    h = 83144.0 / R_FLOAT
    usp = h / -math.log(math.fsum(math.exp(-h / t) for t in temps) / len(temps))
    assert rel_close(mkt, usp, 1e-12)


# =========================================================================================
# Integration
# =========================================================================================


def _int_inputs(v: dict[str, Any]) -> tuple[float, float, float]:
    i = v["inputs"]
    return math.log(i["k_ref_per_s"]), i["activation_energy_j_per_mol"], i["t_ref_k"]


@covers("TV-INT-1")
def test_tv_int_1(vector: Vector, bundle: ConfigBundle) -> None:
    from pharmacy import kinetics

    v = vector("TV-INT-1")
    ln_k, ea, t_ref = _int_inputs(v)
    for key in ("segments", "segments_reversed"):
        segs = v["inputs"][key]
        theta = kinetics.integrated_rate(
            ln_k,
            ea,
            0.0,
            [s["temperature_k"] for s in segs],
            [40.0] * len(segs),
            [s["duration_s"] for s in segs],
            t_ref,
            40.0,
        )
        assert rel_close(theta, v["expected"]["theta_full_precision"], 1e-9), theta
        alpha = kinetics.conversion_from_theta("F1", theta, numerics=bundle.settings["numerics"])
        potency = v["inputs"]["c0_percent"] * (1 - alpha)
        assert rel_close(potency, v["expected"]["potency_percent_full_precision"], 1e-9), potency


@covers("TV-INT-2")
def test_tv_int_2(vector: Vector, bundle: ConfigBundle) -> None:
    from pharmacy import kinetics

    v = vector("TV-INT-2")
    ln_k, ea, t_ref = _int_inputs(v)
    i = v["inputs"]
    k = kinetics.rate_constant(ln_k, ea, 0.0, i["mkt_k_full_precision"], 40.0, t_ref, 40.0)
    alpha = kinetics.conversion_from_theta("F1", k * i["duration_s"], numerics=bundle.settings["numerics"])
    assert rel_close(i["c0_percent"] * (1 - alpha), v["expected"]["potency_percent_full_precision"], 1e-9)


SEPARABLE_KINDS: list[tuple[RateLawKind, dict[str, float]]] = [
    ("F0", {}),
    ("F1", {}),
    ("F2", {}),
    ("Fn", {"order_n": 1.5}),
    ("Fn", {"order_n": 0.5}),
    ("R2", {}),
    ("R3", {}),
    ("A2", {}),
    ("A3", {}),
    ("A4", {}),
    ("An", {"avrami_n": 1.5}),
    ("B1", {"alpha0": 0.05}),
    ("D1", {}),
    ("D2", {}),
    ("D3", {}),
    ("D4", {}),
]


@covers("TV-INT-3")
@HYPOTHESIS
@given(
    case=st.sampled_from(SEPARABLE_KINDS),
    segments=st.lists(
        st.tuples(st.floats(270.0, 330.0), st.floats(0.0, 100.0), st.floats(1.0, 3e6)), min_size=1, max_size=8
    ),
    split=st.floats(0.01, 0.99),
    ln_k=st.floats(-24.0, -15.0),
)
def test_tv_int_3_split_and_order_invariance(
    case: tuple[RateLawKind, dict[str, float]],
    segments: list[tuple[float, float, float]],
    split: float,
    ln_k: float,
) -> None:
    from pharmacy import kinetics

    numerics = _numerics()
    kind, params = case

    def alpha_of(segs: list[tuple[float, float, float]]) -> float:
        theta = kinetics.integrated_rate(
            ln_k,
            90000.0,
            0.03,
            [s[0] for s in segs],
            [s[1] for s in segs],
            [s[2] for s in segs],
            298.15,
            40.0,
        )
        return float(kinetics.conversion_from_theta(kind, theta, numerics=numerics, **params))

    base = alpha_of(segments)
    first = segments[0]
    split_segments = [
        (first[0], first[1], first[2] * split),
        (first[0], first[1], first[2] * (1 - split)),
        *segments[1:],
    ]
    for other in (alpha_of(split_segments), alpha_of(list(reversed(segments)))):
        assert abs(other - base) <= 1e-12 * abs(base), (kind, params, base, other)


@covers("TV-INT-4")
def test_tv_int_4(vector: Vector) -> None:
    """1e-8 relative against the analytic value (spec 15.1). The vector's observed-order
    remark applies to fixed-step solvers; integrate_ode_reference is adaptive, so the
    order is not asserted."""
    from pharmacy import kinetics

    v = vector("TV-INT-4")
    ln_k, ea, t_ref = _int_inputs(v)
    segs = v["inputs"]["segments"]
    ks = [kinetics.rate_constant(ln_k, ea, 0.0, s["temperature_k"], 40.0, t_ref, 40.0) for s in segs]
    alpha = kinetics.integrate_ode_reference("F1", ks, [s["duration_s"] for s in segs])
    potency = v["inputs"]["c0_percent"] * (1 - alpha)
    assert within(potency, v["expected"]["potency_percent_full_precision"], v["tolerance"])


# =========================================================================================
# Arrhenius fits
# =========================================================================================

RATE_UNIT_SECONDS = {"k_per_min": 60.0, "k_per_h": 3600.0, "k_per_s": 1.0, "k_ml_per_mg_per_h": 3600.0}


def _rates(inputs: dict[str, Any]) -> list[float]:
    for key, seconds in RATE_UNIT_SECONDS.items():
        if key in inputs:
            return [k / seconds for k in inputs[key]]
    raise KeyError("no rate column")


ARRHENIUS_IDS = [
    "TV-KIN-1",
    "TV-KIN-2",
    "TV-KIN-3-1",
    "TV-KIN-3-2",
    "TV-KIN-3-3",
    "TV-KIN-3-4",
    "TV-KIN-3-5",
    "TV-KIN-3-6",
    "TV-KIN-3-7",
]


@covers(*ARRHENIUS_IDS)
@pytest.mark.parametrize("vector_id", ARRHENIUS_IDS)
def test_arrhenius_vectors(vector: Vector, vector_id: str) -> None:
    from pharmacy import fit

    v = vector(vector_id)
    i = v["inputs"]
    result = fit.fit_arrhenius_two_stage(
        i["temperatures_k"], _rates(i), 298.15, i["gas_constant_j_per_mol_k"]
    )
    ea_kj = result.ea_j_per_mol / 1000.0
    foundation = v["our_recomputation"]["foundation_ea_j_per_mol"]
    assert within(ea_kj, v["expected"]["activation_energy_kj_per_mol"], v["tolerance"]), (
        f"{vector_id}: Ea {ea_kj} kJ/mol vs published {v['expected']['activation_energy_kj_per_mol']} "
        f"(foundation {foundation / 1000.0})"
    )


@covers("TV-KIN-3x")
def test_tv_kin_3x_known_mismatch(vector: Vector) -> None:
    """Documented behaviour: the refit gives 70.2 +- 14.1 kJ/mol; the published 55.46 is
    never asserted."""
    from pharmacy import fit

    v = vector("TV-KIN-3x")
    i = v["inputs"]
    result = fit.fit_arrhenius_two_stage(
        i["temperatures_k"], _rates(i), 298.15, i["gas_constant_j_per_mol_k"]
    )
    documented = v["our_recomputation"]
    assert round(result.ea_j_per_mol / 1000.0, 1) == documented["activation_energy_kj_per_mol"]
    assert round(result.ea_se_j_per_mol / 1000.0, 1) == documented["activation_energy_se_kj_per_mol"]


# =========================================================================================
# Rate-constant vectors
# =========================================================================================


@covers("TV-KIN-3e")
def test_tv_kin_3e(vector: Vector) -> None:
    from pharmacy import kinetics

    v = vector("TV-KIN-3e")
    i = v["inputs"]
    names = {"Amoxicillin": "amoxicillin_t90_h", "Clavulanate": "clavulanate_t90_h"}
    for case in i["cases"]:
        key = names[case["series"].split()[0]]
        for t_c in case["temperatures_c"]:
            k = kinetics.rate_constant(
                math.log(case["k_ref_per_h"]),
                case["activation_energy_j_per_mol"],
                0.0,
                t_c + ZERO_C,
                40.0,
                i["reference_temperature_k"],
                40.0,
                i["gas_constant_j_per_mol_k"],
            )
            t90 = math.log(10 / 9) / k
            assert within(t90, v["expected"][key][f"{int(t_c)}_c"], v["tolerance"]), (key, t_c, t90)


@covers("TV-KIN-5")
def test_tv_kin_5(vector: Vector) -> None:
    """k = A exp(-Ea/(R T)) exp(m RH_fraction) expressed in the engine's reference form:
    ln k* = ln A - Ea/(R T*) + m RH*/100 and B = m/100 per %RH."""
    from pharmacy import kinetics

    v = vector("TV-KIN-5")
    i = v["inputs"]
    r = i["gas_constant_j_per_mol_k"]
    t_ref, rh_ref = 298.15, 40.0
    ln_k_ref = (
        math.log(i["a_per_h"])
        - i["activation_energy_j_per_mol"] / (r * t_ref)
        + i["m_humidity"] * rh_ref / 100
    )
    for condition, expected in zip(i["conditions"], v["expected"]["k_per_h"], strict=True):
        k = kinetics.rate_constant(
            ln_k_ref,
            i["activation_energy_j_per_mol"],
            i["m_humidity"] / 100,
            condition["temperature_k"],
            condition["rh_percent"],
            t_ref,
            rh_ref,
            r,
        )
        assert within(k, expected, v["tolerance"]), (condition, k)


@covers("TV-H-2")
def test_tv_h_2_known_mismatch(vector: Vector) -> None:
    """Documented behaviour: 5.0 / exp(0.09 x 15) = 1.296 years; never the published 1.2."""
    from pharmacy import kinetics

    v = vector("TV-H-2")
    i = v["inputs"]
    ratio = kinetics.rate_constant(
        0.0, 0.0, i["b_per_percent_rh"], 298.15, i["rh_to_percent"], 298.15, i["rh_from_percent"]
    )
    years = i["shelf_life_years_at_60"] / ratio
    assert within(years, v["expected"]["shelf_life_years_at_75"], v["tolerance"])
    assert rel_close(years, v["our_recomputation"]["foundation_shelf_life_years"], 1e-9)
    assert round(years, 1) != 1.2


@covers("TV-H-3")
def test_tv_h_3(vector: Vector) -> None:
    from pharmacy import kinetics

    v = vector("TV-H-3")
    i = v["inputs"]
    t_ref, rh_ref = 298.15, 40.0
    ea_over_r = -i["minus_ea_over_r_k"]
    ln_k_ref = i["intercept_ln_k"] - ea_over_r / t_ref + i["b_per_percent_rh"] * rh_ref
    for condition, expected in zip(i["conditions"], v["expected"]["k"], strict=True):
        k = kinetics.rate_constant(
            ln_k_ref,
            ea_over_r * R_FLOAT,
            i["b_per_percent_rh"],
            condition["temperature_k"],
            condition["rh_percent"],
            t_ref,
            rh_ref,
        )
        assert within(k, expected, v["tolerance"]), (condition, k)
    assert within(ea_over_r * R_FLOAT / 1000, v["expected"]["activation_energy_kj_per_mol"], {"abs": 0.05})


@covers("TV-H-4")
def test_tv_h_4(vector: Vector) -> None:
    from pharmacy import kinetics
    from pharmacy.constants import THERMOCHEMICAL_CALORIE_J

    v = vector("TV-H-4")
    i = v["inputs"]
    r = i["gas_constant_cal_per_mol_k"] * THERMOCHEMICAL_CALORIE_J
    ea = i["activation_energy_kcal_per_mol"] * 1000 * THERMOCHEMICAL_CALORIE_J
    t_ref, rh_ref = 298.15, 40.0
    ln_k_ref = i["ln_a"] - ea / (r * t_ref) + i["b_per_percent_rh"] * rh_ref
    for condition, expected in zip(i["conditions"], v["expected"]["shelf_life"], strict=True):
        k_per_day = kinetics.rate_constant(
            ln_k_ref,
            ea,
            i["b_per_percent_rh"],
            condition["temperature_c"] + ZERO_C,
            condition["rh_percent"],
            t_ref,
            rh_ref,
            r,
        )
        days = i["specification_percent"] / k_per_day
        value = days if expected["unit"] == "days" else days / 365.25
        assert within(value, expected["value"], v["tolerance"]), (condition, value)


@covers("TV-H-1")
def test_tv_h_1(vector: Vector) -> None:
    v = vector("TV-H-1")
    for pair, expected in zip(v["inputs"]["pairs"], v["expected"]["ratios"], strict=True):
        ratio = targets.water_loss_ratio(pair["rh_alternative_percent"], pair["rh_reference_percent"])
        assert within(float(ratio), expected, v["tolerance"])


# =========================================================================================
# Whole-study fits (plausibility only, or skipped)
# =========================================================================================


@covers("TV-KIN-4")
def test_tv_kin_4_negative_b_plausibility(vector: Vector) -> None:
    """Known mismatch (equation S1 unavailable): only the sign of B is asserted. The
    degradant DP-O is fitted as zero-order loss of 100 - DP-O percent."""
    from pharmacy import fit

    v = vector("TV-KIN-4")
    observations = [
        fit.StudyObservation(
            o["time_weeks"] * 7 * 86400.0,
            o["temperature_c"] + ZERO_C,
            float(o["rh_percent"]),
            100.0 - o["degradant_dp_o_percent"],
            None,
            "dp-o",
        )
        for o in v["inputs"]["observations"]
    ]
    result = fit.fit_stability_study(
        observations,
        "F0",
        298.15,
        40.0,
        fit_humidity=True,
        fit_initial=False,
        absolute_weights=False,
        initial_percent=100.0,
    )
    b = result.mean[result.names.index("b_per_percent_rh")]
    assert b < 0, f"fitted B {b} is not negative"


TV_H_5_EA_KCAL_RANGE = (10.0, 50.0)
TV_H_5_B_RANGE = (0.0, 0.10)


@covers("TV-H-5")
def test_tv_h_5_plausibility(vector: Vector) -> None:
    """Known mismatch: "fitted Ea and B of the right size" only. The range is fixed here
    before running: Ea in [10, 50] kcal/mol and B in (0, 0.10] per %RH for each lot (the
    usual size of solid-state Ea and of Waterman's humidity sensitivity); the published
    means and sd are never asserted."""
    from pharmacy import fit
    from pharmacy.constants import THERMOCHEMICAL_CALORIE_J
    from pharmacy.errors import InputError

    v = vector("TV-H-5")
    conditions = {"25_60": (298.15, 60.0), "30_65": (303.15, 65.0), "40_75": (313.15, 75.0)}
    for lot in v["inputs"]["lots"]:
        observations = [fit.StudyObservation(0.0, 298.15, 60.0, 100.0, None, "t0")]
        for key, value in lot["degradant_6_months_percent"].items():
            t, rh = conditions[key]
            observations.append(fit.StudyObservation(6 * 2629800.0, t, rh, 100.0 - value, None, key))
        try:
            result = fit.fit_stability_study(
                observations,
                "F0",
                298.15,
                40.0,
                fit_humidity=True,
                fit_initial=False,
                absolute_weights=False,
                initial_percent=100.0,
            )
        except InputError as error:
            pytest.fail(
                f"TV-H-5 lot {lot['lot']}: fit_stability_study refuses the table ({error}). The lot has "
                "n = p + 1 points, so the AICc correction 2p(p+1)/(n - p - 1) divides by zero; "
                "spec 14.3 requires a fit with null AICc when n <= p + 1."
            )
        ea_kcal = result.mean[result.names.index("ea_j_per_mol")] / 1000 / THERMOCHEMICAL_CALORIE_J
        b = result.mean[result.names.index("b_per_percent_rh")]
        assert TV_H_5_EA_KCAL_RANGE[0] <= ea_kcal <= TV_H_5_EA_KCAL_RANGE[1], (lot["lot"], ea_kcal)
        assert TV_H_5_B_RANGE[0] < b <= TV_H_5_B_RANGE[1], (lot["lot"], b)


@covers("TV-FIT-1")
def test_tv_fit_1_unverified(vector: Vector) -> None:
    v = vector("TV-FIT-1")
    assert v["status"] == "unverified"
    pytest.skip(f"TV-FIT-1 unverified: {v['note']}")


# =========================================================================================
# First-order log-linear fits
# =========================================================================================


@covers("TV-KIN-6")
def test_tv_kin_6(vector: Vector) -> None:
    from pharmacy import fit

    v = vector("TV-KIN-6")
    i, e = v["inputs"], v["expected"]
    free = fit.fit_first_order_loglinear(i["times_h"], i["percent_remaining"])
    assert rel_close(free.k, e["k_free_per_h_full_precision"], 1e-9), free.k
    assert rel_close(free.k_se, e["k_free_se_per_h_full_precision"], 1e-9), free.k_se
    assert rel_close(free.c0_percent, e["c0_free_percent_full_precision"], 1e-9), free.c0_percent
    fixed = fit.fit_first_order_loglinear(i["times_h"], i["percent_remaining"], fixed_c0_percent=100.0)
    assert rel_close(fixed.k, e["k_fixed_c0_100_per_h_full_precision"], 1e-9), fixed.k
    assert fixed.c0_se_percent is None
    for value, key in (
        (free.k, "k_free_per_h"),
        (free.k_se, "k_free_se_per_h"),
        (free.c0_percent, "c0_free_percent"),
        (fixed.k, "k_fixed_c0_100_per_h"),
    ):
        assert holds_to_rounding(value, e[key]), (key, value, e[key])


@covers("TV-RAD-1")
def test_tv_rad_1(vector: Vector) -> None:
    from pharmacy import fit

    v = vector("TV-RAD-1")
    i = v["inputs"]
    result = fit.fit_first_order_loglinear(
        i["dose_kgy"], i["concentration_ug_per_ml"], fixed_c0_percent=i["concentration_ug_per_ml"][0]
    )
    assert within(result.k, v["expected"]["k_per_kgy"], v["tolerance"]), result.k


# =========================================================================================
# Constants, numerics, regulatory
# =========================================================================================


@covers("TV-KIN-7")
def test_tv_kin_7(vector: Vector) -> None:
    from pharmacy.constants import THERMOCHEMICAL_CALORIE_J

    v = vector("TV-KIN-7")
    assert v["inputs"]["kj_per_kcal"] == THERMOCHEMICAL_CALORIE_J
    for conversion, expected in zip(v["inputs"]["conversions"], v["expected"]["converted"], strict=True):
        if conversion["from_unit"] == "kcal_per_mol":
            value = conversion["value"] * THERMOCHEMICAL_CALORIE_J
        else:
            value = conversion["value"] / THERMOCHEMICAL_CALORIE_J
        assert within(value, expected["value"], v["tolerance"])
    d = v["inputs"]["delta_h_over_r"]
    ratio = targets.delta_h_over_r(
        MP.mpf(str(d["delta_h_kj_per_mol"])), MP.mpf(str(d["gas_constant_kj_per_mol_k"]))
    )
    assert within(float(ratio), v["expected"]["delta_h_over_r_k"], v["tolerance"])


@covers("TV-GCI-1", "TV-GCI-2", "TV-GCI-3")
@pytest.mark.parametrize("vector_id", ["TV-GCI-1", "TV-GCI-2", "TV-GCI-3"])
def test_gci(vector: Vector, vector_id: str) -> None:
    v = vector(vector_id)
    i = v["inputs"]
    result = targets.gci(i["phi1"], i["phi2"], i["phi3"], i["r21"], i["r32"])
    for key, published in v["expected"].items():
        assert within(float(result[key]), published, v["tolerance"]), (key, float(result[key]), published)
        assert holds_to_rounding(float(result[key]), v["our_recomputation"][key]), (key, float(result[key]))
        assert rel_close(float(result[key]), v["our_recomputation"]["foundation_values"][key], 1e-12)


@covers("TV-MC-1")
def test_tv_mc_1(vector: Vector) -> None:
    v = vector("TV-MC-1")
    n = targets.coverage_simulation_count(
        MP.mpf(str(v["inputs"]["expected_coverage"])), MP.mpf(str(v["inputs"]["target_mcse"]))
    )
    assert within(float(n), v["expected"]["n_sim"], v["tolerance"])


@covers("TV-MC-2")
def test_tv_mc_2(vector: Vector) -> None:
    from pharmacy import uncertainty

    v = vector("TV-MC-2")
    p = v["inputs"]["coverage_probability"]
    assert uncertainty.jcgm_batch_size(p) == v["expected"]["batch_m"]
    assert exact.jcgm_batch_size(p) == v["expected"]["batch_m"]
    assert math.ceil(100 / (1 - Fraction(repr(p)))) == v["expected"]["j"]
    assert 10**4 / (1 - Fraction(repr(p))) == v["expected"]["minimum_m_non_adaptive"]


@covers("TV-Q1E-1", "TV-Q1E-2", "TV-Q1E-3", "TV-Q1E-4")
@pytest.mark.parametrize("vector_id", ["TV-Q1E-1", "TV-Q1E-2", "TV-Q1E-3", "TV-Q1E-4"])
def test_q1e(vector: Vector, vector_id: str) -> None:
    from pharmacy import decision

    v = vector(vector_id)
    i = v["inputs"]
    assert decision.q1e_max_shelf_life_months(i["case"], i["x_months"]) == v["expected"]["y_max_months"]


@covers("TV-Q1E-5")
def test_q1e_5_documentation(vector: Vector) -> None:
    """Spec 15.1: the decision documentation states the poolability test order (slopes,
    then intercepts) and its significance level 0.25."""
    from pharmacy import decision

    v = vector("TV-Q1E-5")
    public = [
        obj
        for name, obj in vars(decision).items()
        if not name.startswith("_") and getattr(obj, "__module__", "") == decision.__name__
    ]
    text = " ".join(filter(None, [decision.__doc__, *(inspect.getdoc(obj) for obj in public)]))
    level = str(v["expected"]["significance_level"])
    assert level in text, f"decision documentation does not state the significance level {level}"
    order = v["inputs"]["test_order"]
    positions = [text.find(word) for word in order]
    assert all(p >= 0 for p in positions), f"test order {order} not documented"
    assert positions == sorted(positions), f"test order {order} documented out of order"


@covers("TV-Q3B-1")
def test_q3b_1(vector: Vector) -> None:
    from pharmacy import decision

    v = vector("TV-Q3B-1")
    i = v["inputs"]
    assert (
        decision.q3b_exceeds(i["total_daily_intake_mg"], i["qualification_threshold_tdi_mg"])
        is v["expected"]["qualification_threshold_exceeded"]
    )
    assert round(i["total_daily_intake_mg"]) == v["expected"]["rounded_tdi_mg"]


@covers("TV-Q3B-2")
def test_q3b_2(vector: Vector, bundle: ConfigBundle) -> None:
    from pharmacy import decision

    v = vector("TV-Q3B-2")
    t = decision.q3b_thresholds(v["inputs"]["maximum_daily_dose_mg"], settings=bundle.settings["q3b"])
    e = v["expected"]
    assert within(t.reporting_percent, e["reporting_threshold_percent"], v["tolerance"])
    assert within(t.identification_percent, e["identification_threshold_percent"], v["tolerance"])
    assert within(t.qualification_percent, e["qualification_threshold_percent"], v["tolerance"])
    oracle = exact.q3b_thresholds(v["inputs"]["maximum_daily_dose_mg"])
    for name, value in oracle.items():
        assert getattr(t, name) == (None if value is None else float(value)), (name, getattr(t, name), value)


@covers("TV-Q1B-1")
def test_q1b_1_light_priors_match_confirmatory_doses(vector: Vector, bundle: ConfigBundle) -> None:
    """The light prior medians are 5% loss at one ICH Q1B confirmatory dose (MDS03):
    exp(mu) x dose = -ln(0.95), to the rounding of the configured mu (3 decimals)."""
    v = vector("TV-Q1B-1")
    doses = v["inputs"]["confirmatory_exposure"]
    assert doses["visible_lux_h"] == v["expected"]["visible_dose_lux_h_min"]
    assert doses["near_uv_w_h_per_m2"] == v["expected"]["near_uv_dose_w_h_per_m2_min"]
    light = [c["light_priors"] for c in bundle.reaction_classes["classes"] if c.get("light_priors")]
    assert light, "no class carries light priors"
    target = -math.log(0.95)
    for priors in light:
        assert priors is not None
        vis = math.exp(priors["k_vis_per_lux_h"]["mu"]) * doses["visible_lux_h"]
        uv = math.exp(priors["k_uv_per_w_h_per_m2"]["mu"]) * doses["near_uv_w_h_per_m2"]
        assert abs(vis / target - 1) <= 5e-4, (vis, target)
        assert abs(uv / target - 1) <= 5e-4, (uv, target)


@covers("TV-SF-1")
def test_tv_sf_1(vector: Vector) -> None:
    v = vector("TV-SF-1")
    i, e = v["inputs"], v["expected"]
    ground = float(targets.first_order_half_life(i["k_ground_per_month"]))
    flight = float(targets.first_order_half_life(i["k_flight_per_month"]))
    assert within(ground, e["half_life_ground_months"], v["tolerance"])
    assert within(flight, e["half_life_flight_months"], v["tolerance"])
    assert abs(i["k_flight_per_month"] / i["k_ground_per_month"] - e["rate_ratio"]) <= 0.05


@covers("TV-SF-2")
def test_tv_sf_2_documented_ranges(vector: Vector) -> None:
    """Known mismatch: the validation report (tests/validation, report only) compares the
    predicted failure counts with the documented ranges, never a single number. This
    checks that the ranges it uses are the ones the vector documents: from the grid count
    (9, 23) to the published Table III count (11, 24)."""
    v = vector("TV-SF-2")
    grid = v["our_recomputation"]
    published = v["expected"]["failed_flight"]
    for payload in (2, 4):
        bounds = sorted([grid[f"grid_count_flight_payload_{payload}"], published[payload - 1]])
        assert SF2_DOCUMENTED_RANGES[payload] == tuple(bounds)
    assert SF2_DOCUMENTED_RANGES == {2: (9, 11), 4: (23, 24)}


@covers("TV-RAD-2")
def test_tv_rad_2(vector: Vector, bundle: ConfigBundle) -> None:
    from pharmacy.chemistry import radiolysis

    v = vector("TV-RAD-2")
    limit = v["expected"]["max_loss_fraction_at_1_gy"]
    g = bundle.radiation["direct_effect_g_loss_mol_per_j"]
    f = bundle.settings["radiation"]["dose_rate_safety_factor"]["value"]
    k_upper = radiolysis.upper_bound_k_per_gy(bundle.radiation, "solid")
    solids = [p for p in bundle.formulary["products"] if p["dose_form_family"] == "solid"]
    assert solids
    for product in solids:
        for ingredient in product["ingredients"]:
            k_d = radiolysis.k_dose_direct(math.exp(g["mu"]), ingredient["molar_mass_g_per_mol"]["value"])
            central = radiolysis.central_loss_fraction(k_d, 1.0)
            upper = radiolysis.upper_bound_loss_fraction(k_upper, f, 1.0)
            assert central < limit, (product["product_id"], ingredient["ingredient_id"], "central", central)
            assert upper < limit, (product["product_id"], ingredient["ingredient_id"], "upper", upper)


# =========================================================================================
# Catalogue completeness
# =========================================================================================


def test_every_vector_is_covered() -> None:
    catalogue = set()
    for path in sorted(VECTOR_DIR.glob("vectors-*.json")):
        catalogue |= {v["vector_id"] for v in json.loads(path.read_text(encoding="utf-8"))["vectors"]}
    assert catalogue == COVERED, {
        "missing": sorted(catalogue - COVERED),
        "unknown": sorted(COVERED - catalogue),
    }

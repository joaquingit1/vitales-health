# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Engine kinetics against the oracle at 1e-9 relative (1e-12 absolute near zero), spec
5.1 to 5.4 and 15.2: fixed grids plus Hypothesis (derandomized, 200+ examples)."""

import functools
import math
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.types import NumericsSettings, RateLawKind
from tests.oracle import kinetics as ok
from tests.oracle.compare import assert_agrees
from tests.oracle.precision import MP, R_FLOAT

HYPOTHESIS = settings(derandomize=True, max_examples=200, deadline=None)
KIND_PARAMS: dict[str, dict[str, float]] = {
    "Fn": {"order_n": 1.7},
    "An": {"avrami_n": 1.5},
    "B1": {"alpha0": 0.01},
}
EXTRA_PARAMS: list[tuple[RateLawKind, dict[str, float]]] = [
    ("Fn", {"order_n": 0.5}),
    ("Fn", {"order_n": 2.5}),
    ("An", {"avrami_n": 0.7}),
    ("An", {"avrami_n": 3.3}),
    ("B1", {"alpha0": 0.2}),
    ("B1", {"alpha0": 1e-4}),
]
CASES: list[tuple[RateLawKind, dict[str, float]]] = [
    (k, KIND_PARAMS.get(k, {})) for k in ok.ALL_KINDS
] + EXTRA_PARAMS


def _case_id(case: tuple[RateLawKind, dict[str, float]]) -> str:
    kind, params = case
    return kind + "".join(f"-{k}={v}" for k, v in params.items())


@pytest.fixture(scope="module")
def kin() -> ModuleType:
    import pharmacy.kinetics

    return pharmacy.kinetics


@pytest.fixture(scope="module")
def numerics(bundle: ConfigBundle) -> NumericsSettings:
    return bundle.settings["numerics"]


@functools.cache
def _numerics() -> NumericsSettings:
    """Engine numerics for Hypothesis tests (which cannot take fixtures per example)."""
    return load_bundle().settings["numerics"]


# =========================================================================================
# 5.1, 5.2
# =========================================================================================

RATE_GRID = [
    (-20.0, 100000.0, 0.04, 298.15, 40.0),
    (-20.0, 100000.0, 0.04, 318.15, 75.0),
    (-12.5, 65595.4, 0.0, 268.15, 10.0),
    (-25.0, 120000.0, -0.03, 313.15, 90.0),
    (-15.0, 0.0, 0.1, 400.0, 0.0),
    (-30.0, 250000.0, 0.07, 250.0, 100.0),
]


@pytest.mark.parametrize("args", RATE_GRID)
def test_rate_constant_grid(kin: ModuleType, args: tuple[float, ...]) -> None:
    ln_k, ea, b, t, rh = args
    engine = kin.rate_constant(ln_k, ea, b, t, rh, 298.15, 40.0)
    oracle = ok.rate_constant(ln_k, ea, b, t, rh, 298.15, 40.0)
    assert_agrees("kinetics.rate_constant", {"args": args}, engine, oracle, "5.1")


@HYPOTHESIS
@given(
    ln_k=st.floats(-40.0, -5.0),
    ea=st.floats(0.0, 250000.0),
    b=st.floats(-0.1, 0.1),
    t=st.floats(230.0, 380.0),
    rh=st.floats(0.0, 100.0),
)
def test_rate_constant_hypothesis(ln_k: float, ea: float, b: float, t: float, rh: float) -> None:
    from pharmacy import kinetics

    engine = kinetics.rate_constant(ln_k, ea, b, t, rh, 298.15, 40.0)
    oracle = ok.rate_constant(ln_k, ea, b, t, rh, 298.15, 40.0)
    assert_agrees("kinetics.rate_constant", locals() | {"T*": 298.15, "RH*": 40.0}, engine, oracle, "5.1")


def test_rate_constant_domain_error(kin: ModuleType) -> None:
    from pharmacy.errors import ModelDomainError

    with pytest.raises(ModelDomainError):
        kin.rate_constant(-20.0, 1e5, 0.0, 0.0, 40.0, 298.15, 40.0)
    with pytest.raises(ModelDomainError):
        kin.rate_constant(-20.0, 1e5, 0.0, 298.15, 40.0, -1.0, 40.0)


@HYPOTHESIS
@given(
    segments=st.lists(
        st.tuples(st.floats(260.0, 330.0), st.floats(0.0, 100.0), st.floats(0.0, 3.0e6)),
        min_size=1,
        max_size=12,
    ),
    ln_k=st.floats(-30.0, -8.0),
    ea=st.floats(0.0, 200000.0),
    b=st.floats(-0.08, 0.08),
)
def test_integrated_rate_hypothesis(
    segments: list[tuple[float, float, float]], ln_k: float, ea: float, b: float
) -> None:
    from pharmacy import kinetics

    temps = [s[0] for s in segments]
    rhs = [s[1] for s in segments]
    durations = [s[2] for s in segments]
    engine = kinetics.integrated_rate(ln_k, ea, b, temps, rhs, durations, 298.15, 40.0)
    oracle = ok.integrated_rate(ln_k, ea, b, temps, rhs, durations, 298.15, 40.0)
    assert_agrees("kinetics.integrated_rate", locals(), engine, oracle, "5.2")


def test_integrated_rate_empty_and_errors(kin: ModuleType) -> None:
    from pharmacy.errors import InputError

    assert kin.integrated_rate(-20.0, 1e5, 0.0, [], [], [], 298.15, 40.0) == 0.0
    with pytest.raises(InputError):
        kin.integrated_rate(-20.0, 1e5, 0.0, [300.0], [40.0, 50.0], [1.0], 298.15, 40.0)
    with pytest.raises(InputError):
        kin.integrated_rate(-20.0, 1e5, 0.0, [300.0], [40.0], [-1.0], 298.15, 40.0)


def test_gas_constant_default_is_codata(kin: ModuleType) -> None:
    import inspect

    default = inspect.signature(kin.rate_constant).parameters["gas_constant_j_per_mol_k"].default
    assert default == R_FLOAT


# =========================================================================================
# 5.3 table 5.3.1
# =========================================================================================


def _theta_grid(kind: RateLawKind, params: dict[str, float]) -> list[float]:
    complete = ok.completion_theta(kind, **params)
    grid = [
        0.0,
        1e-12,
        1e-9,
        1e-6,
        1e-3,
        0.01,
        0.05,
        0.1,
        0.2,
        0.3,
        0.5,
        0.75,
        1.0,
        1.5,
        2.0,
        3.0,
        5.0,
        7.5,
        10.0,
    ]
    if not MP.isinf(complete):
        c = float(complete)
        grid += [c * f for f in (0.5, 0.9, 0.99, 0.999999, 1.0)] + [c * 1.5]
    return sorted(set(grid))


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_conversion_from_theta_grid(
    kin: ModuleType, numerics: NumericsSettings, case: tuple[RateLawKind, dict[str, float]]
) -> None:
    kind, params = case
    for theta in _theta_grid(kind, params):
        engine = kin.conversion_from_theta(kind, theta, numerics=numerics, **params)
        oracle = ok.conversion_from_theta(kind, theta, **params)
        assert_agrees(
            "kinetics.conversion_from_theta", {"kind": kind, "theta": theta, **params}, engine, oracle, "5.3"
        )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_conversion_array_matches_scalar(
    kin: ModuleType, numerics: NumericsSettings, case: tuple[RateLawKind, dict[str, float]]
) -> None:
    import numpy as np

    kind, params = case
    grid = _theta_grid(kind, params)
    array = kin.conversion_from_theta_array(kind, np.asarray(grid), numerics=numerics, **params)
    scalars = [kin.conversion_from_theta(kind, t, numerics=numerics, **params) for t in grid]
    assert list(array) == scalars


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_theta_from_conversion_grid(kin: ModuleType, case: tuple[RateLawKind, dict[str, float]]) -> None:
    kind, params = case
    alphas = [0.0, 1e-12, 1e-8, 1e-4, 0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999999]
    if MP.isfinite(ok.completion_theta(kind, **params)):
        alphas.append(1.0)
    for alpha in alphas:
        engine = kin.theta_from_conversion(kind, alpha, **params)
        oracle = ok.theta_from_conversion(kind, alpha, **params)
        assert_agrees(
            "kinetics.theta_from_conversion", {"kind": kind, "alpha": alpha, **params}, engine, oracle, "5.3"
        )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_rate_function_grid(kin: ModuleType, case: tuple[RateLawKind, dict[str, float]]) -> None:
    kind, params = case
    for alpha in [1e-9, 1e-4, 0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999999]:
        engine = kin.rate_function(kind, alpha, **params)
        oracle = ok.rate_function(kind, alpha, **params)
        assert_agrees(
            "kinetics.rate_function", {"kind": kind, "alpha": alpha, **params}, engine, oracle, "5.3"
        )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_completion_theta(kin: ModuleType, case: tuple[RateLawKind, dict[str, float]]) -> None:
    kind, params = case
    engine = kin.completion_theta(kind, **params)
    oracle = ok.completion_theta(kind, **params)
    if MP.isinf(oracle):
        assert engine == math.inf
    else:
        assert_agrees(
            "kinetics.completion_theta", {"kind": kind, **params}, engine, oracle, "5.3 table 5.3.1"
        )


@HYPOTHESIS
@given(
    kind=st.sampled_from(ok.ALL_KINDS),
    theta=st.floats(0.0, 10.0),
    order_n=st.floats(0.2, 3.0).filter(lambda n: abs(n - 1) > 1e-3),
    avrami_n=st.floats(0.5, 4.0),
    alpha0=st.floats(1e-4, 0.5),
)
def test_conversion_from_theta_hypothesis(
    kind: RateLawKind, theta: float, order_n: float, avrami_n: float, alpha0: float
) -> None:
    from pharmacy import kinetics

    params: dict[str, float] = {
        "Fn": {"order_n": order_n},
        "An": {"avrami_n": avrami_n},
        "B1": {"alpha0": alpha0},
    }.get(kind, {})
    engine = kinetics.conversion_from_theta(kind, theta, numerics=_numerics(), **params)
    oracle = ok.conversion_from_theta(kind, theta, **params)
    assert_agrees(
        "kinetics.conversion_from_theta", {"kind": kind, "theta": theta, **params}, engine, oracle, "5.3"
    )
    if 0 < engine < 1:
        back = kinetics.theta_from_conversion(kind, engine, **params)
        oracle_back = ok.theta_from_conversion(kind, engine, **params)
        assert_agrees(
            "kinetics.theta_from_conversion",
            {"kind": kind, "alpha": engine, **params},
            back,
            oracle_back,
            "5.3",
        )
        f = kinetics.rate_function(kind, engine, **params)
        assert_agrees(
            "kinetics.rate_function",
            {"kind": kind, "alpha": engine, **params},
            f,
            ok.rate_function(kind, engine, **params),
            "5.3",
        )


@pytest.mark.parametrize(
    ("kind", "params", "theta"),
    [
        ("Fn", {"order_n": 1.0}, 0.1),
        ("Fn", {"order_n": 0.0}, 0.1),
        ("An", {"avrami_n": 0.0}, 0.1),
        ("An", {}, 0.1),
        ("B1", {}, 0.1),
        ("B1", {"alpha0": 1.0}, 0.1),
        ("F1", {}, -1e-3),
    ],
)
def test_conversion_domain_errors(
    kin: ModuleType, numerics: NumericsSettings, kind: RateLawKind, params: dict[str, float], theta: float
) -> None:
    from pharmacy.errors import ModelDomainError

    with pytest.raises(ModelDomainError):
        kin.conversion_from_theta(kind, theta, numerics=numerics, **params)


def test_large_theta_saturates_without_overflow(kin: ModuleType, numerics: NumericsSettings) -> None:
    for kind, params in CASES:
        value = kin.conversion_from_theta(kind, 1e300, numerics=numerics, **params)
        assert value == 1.0 or 1.0 - value < 1e-12, (kind, value)


# =========================================================================================
# 5.4 humidity integral
# =========================================================================================

J_X = [
    -50.0,
    -30.0,
    -10.0,
    -5.000001,
    -5.0,
    -4.999999,
    -2.0,
    -0.5,
    -1e-8,
    0.0,
    1e-8,
    0.5,
    2.0,
    5.0,
    10.0,
    30.0,
    50.0,
]
J_RATIO = [1e-6, 1e-4, 1e-2, 0.1, 1.0, 3.0, 10.0, 100.0, 1e3]


@pytest.mark.parametrize("x", J_X)
def test_humidity_integral_grid(kin: ModuleType, numerics: NumericsSettings, x: float) -> None:
    tau = 2.7e6
    for ratio in J_RATIO:
        duration = ratio * tau
        engine = kin.humidity_integral(
            x,
            duration,
            tau,
            rel_tol=numerics["series_relative_tolerance"]["value"],
            max_terms=int(numerics["series_max_terms"]["value"]),
        )
        oracle = ok.humidity_integral(x, duration, tau)
        assert_agrees(
            "kinetics.humidity_integral", {"x": x, "D": duration, "tau_m": tau}, engine, oracle, "5.4"
        )


@HYPOTHESIS
@given(x=st.floats(-50.0, 50.0), log_ratio=st.floats(-6.0, 3.0), tau=st.floats(10.0, 1e8))
def test_humidity_integral_hypothesis(x: float, log_ratio: float, tau: float) -> None:
    from pharmacy import kinetics

    duration = tau * 10.0**log_ratio
    engine = kinetics.humidity_integral(x, duration, tau)
    oracle = ok.humidity_integral(x, duration, tau)
    assert_agrees("kinetics.humidity_integral", {"x": x, "D": duration, "tau_m": tau}, engine, oracle, "5.4")
    assert (
        duration * math.exp(min(x, 0.0)) * (1 - 1e-12)
        <= engine
        <= duration * math.exp(max(x, 0.0)) * (1 + 1e-12)
    )


def test_humidity_integral_limits(kin: ModuleType) -> None:
    assert_agrees(
        "kinetics.humidity_integral",
        {"tau": "inf"},
        kin.humidity_integral(0.7, 100.0, math.inf),
        ok.humidity_integral(0.7, 100.0, MP.inf),
        "5.4",
    )
    assert kin.humidity_integral(0.7, 100.0, 0.0) == 100.0
    assert kin.humidity_integral(0.0, 100.0, 50.0) == 100.0


def test_humidity_integral_series_cap_raises(kin: ModuleType) -> None:
    from pharmacy.errors import ConvergenceError

    with pytest.raises(ConvergenceError):
        kin.humidity_integral(40.0, 1e6, 1e5, rel_tol=1e-15, max_terms=3)


# =========================================================================================
# Vector cross-checks at full precision (TV-INT-1, TV-INT-2)
# =========================================================================================


def test_tv_int_1_against_oracle(kin: ModuleType, vector: Any) -> None:
    v = vector("TV-INT-1")["inputs"]
    ln_k = math.log(v["k_ref_per_s"])
    temps = [s["temperature_k"] for s in v["segments"]]
    durations = [s["duration_s"] for s in v["segments"]]
    engine = kin.integrated_rate(
        ln_k, v["activation_energy_j_per_mol"], 0.0, temps, [40.0] * 2, durations, v["t_ref_k"], 40.0
    )
    oracle = ok.integrated_rate(
        ln_k, v["activation_energy_j_per_mol"], 0.0, temps, [40.0] * 2, durations, v["t_ref_k"], 40.0
    )
    assert_agrees("kinetics.integrated_rate", {"vector": "TV-INT-1"}, engine, oracle, "5.2")

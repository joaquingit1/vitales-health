"""Property tests of kinetics (spec 5.11, 15.3): G(g(alpha)) = alpha, G increasing, Theta
order independent and split invariant (TV-INT-3), J bounds, potency non-increasing in time,
rho in [1/2, f0] and non-increasing, and evaluate_draws split invariance."""

import dataclasses
import math
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from pharmacy import kinetics, microclimate
from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.types import NumericsSettings, RateLawKind
from tests.unit.core import builders as b

BUNDLE: ConfigBundle = load_bundle()
NUMERICS: NumericsSettings = BUNDLE.settings["numerics"]
ZONES = {zone["zone_id"]: zone for zone in BUNDLE.zones["zones"]}
PACKAGES = {package["package_id"]: package for package in BUNDLE.packages["packages"]}

KIND_PARAMS: list[tuple[RateLawKind, dict[str, float]]] = [
    ("F0", {}),
    ("F1", {}),
    ("F2", {}),
    ("Fn", {"order_n": 0.5}),
    ("Fn", {"order_n": 1.7}),
    ("R2", {}),
    ("R3", {}),
    ("A2", {}),
    ("A3", {}),
    ("A4", {}),
    ("An", {"avrami_n": 0.8}),
    ("B1", {"alpha0": 0.02}),
    ("D1", {}),
    ("D2", {}),
    ("D3", {}),
    ("D4", {}),
]
kinds = st.sampled_from(KIND_PARAMS)
alphas = st.floats(min_value=1e-12, max_value=0.999, allow_nan=False)
thetas = st.floats(min_value=0.0, max_value=5.0, allow_nan=False)


@given(kinds, alphas)
def test_inverse_round_trip(kind: tuple[RateLawKind, dict[str, float]], alpha: float) -> None:
    name, params = kind
    theta = kinetics.theta_from_conversion(name, alpha, **params)
    assert kinetics.conversion_from_theta(name, theta, numerics=NUMERICS, **params) == pytest.approx(
        alpha, rel=1e-12
    )


@given(kinds, thetas, thetas)
def test_conversion_is_non_decreasing(kind: tuple[RateLawKind, dict[str, float]], a: float, c: float) -> None:
    name, params = kind
    lo, hi = sorted((a, c))
    first = kinetics.conversion_from_theta(name, lo, numerics=NUMERICS, **params)
    second = kinetics.conversion_from_theta(name, hi, numerics=NUMERICS, **params)
    assert 0.0 <= first <= second <= 1.0


@given(
    kinds,
    st.floats(min_value=-22.0, max_value=-14.0),
    st.floats(min_value=40000.0, max_value=140000.0),
    st.floats(min_value=270.0, max_value=330.0),
    st.floats(min_value=1.0, max_value=1e7),
    st.lists(st.floats(min_value=0.01, max_value=1.0), min_size=2, max_size=12),
)
def test_tv_int_3_split_invariance(
    kind: tuple[RateLawKind, dict[str, float]],
    ln_k: float,
    ea: float,
    temperature: float,
    duration: float,
    weights: list[float],
) -> None:
    """Splitting one constant step into N sub-steps leaves alpha unchanged (1e-12 relative)."""
    name, params = kind
    total = math.fsum(weights)
    parts = [duration * w / total for w in weights]
    whole = kinetics.integrated_rate(ln_k, ea, 0.02, [temperature], [55.0], [duration], 298.15, 40.0)
    split = kinetics.integrated_rate(
        ln_k, ea, 0.02, [temperature] * len(parts), [55.0] * len(parts), parts, 298.15, 40.0
    )
    assert split == pytest.approx(whole, rel=1e-12)
    a_whole = kinetics.conversion_from_theta(name, whole, numerics=NUMERICS, **params)
    a_split = kinetics.conversion_from_theta(name, split, numerics=NUMERICS, **params)
    assert a_split == pytest.approx(a_whole, rel=1e-12, abs=1e-300)


@given(
    st.lists(
        st.tuples(
            st.floats(min_value=250.0, max_value=340.0),
            st.floats(min_value=0.0, max_value=100.0),
            st.floats(min_value=0.0, max_value=1e6),
        ),
        min_size=1,
        max_size=15,
    ),
    st.randoms(use_true_random=False),
)
def test_theta_is_order_independent(steps: list[tuple[float, float, float]], shuffler: Any) -> None:
    def theta(items: list[tuple[float, float, float]]) -> float:
        return kinetics.integrated_rate(
            -18.0,
            90000.0,
            0.03,
            [t for t, _, _ in items],
            [r for _, r, _ in items],
            [d for _, _, d in items],
            298.15,
            40.0,
        )

    permuted = list(steps)
    shuffler.shuffle(permuted)
    assert theta(permuted) == pytest.approx(theta(steps), rel=1e-12)


@given(
    st.floats(min_value=-50.0, max_value=50.0),
    st.floats(min_value=1e-3, max_value=1e7),
    st.floats(min_value=1e-6, max_value=1e3),
)
def test_humidity_integral_bounds(x: float, tau: float, ratio: float) -> None:
    duration = ratio * tau
    value = kinetics.humidity_integral(x, duration, tau)
    lo = duration * math.exp(min(x, 0.0))
    hi = duration * math.exp(max(x, 0.0))
    assert lo * (1.0 - 1e-12) <= value <= hi * (1.0 + 1e-12)


@given(
    st.lists(st.floats(min_value=0.0, max_value=200.0), min_size=1, max_size=30),
    st.floats(min_value=0.5, max_value=1.0),
)
def test_active_fraction_bounds(theta: list[float], f0: float) -> None:
    ordered = np.sort(np.array(theta))
    rho = kinetics.active_fraction(ordered, f0)
    assert bool(np.all(rho >= 0.5 - 1e-15))
    assert bool(np.all(rho <= f0 + 1e-15))
    assert bool(np.all(np.diff(rho) <= 0.0))


segment_values = st.tuples(
    st.floats(min_value=275.0, max_value=320.0),
    st.floats(min_value=5.0, max_value=95.0),
    st.floats(min_value=600.0, max_value=86400.0),
)
packages = st.sampled_from(["original_blister", "repackaged_bag", "original_vial", "original_bottle"])


def _segments(values: list[tuple[float, float, float]], package: str) -> list[Any]:
    out = []
    clock = 0.0
    for temperature, rh, duration in values:
        out.append(
            b.segment(clock, clock + duration, package=package, temperature=temperature, rh=rh, dose=1e-8)
        )
        clock += duration
    return out


def _evaluate(model: Any, segments: list[Any], draws: Any) -> Any:
    found = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(found.segments, PACKAGES)
    tracks = microclimate.initial_rh_of_first_package(found.segments, PACKAGES) is None
    steps = {
        "station_kit": microclimate.unit_step_response(
            found.segments, trace.time_constant_s, "station_kit", first_start_tracks_outside=tracks
        )
    }
    return kinetics.evaluate_draws(model, found, trace, steps, draws, ZONES, PACKAGES, BUNDLE.settings)


MODEL = b.thermal_model(ln_k=-16.0, ea=85000.0, b=0.035)
DRAWS = b.fixed_draws(
    MODEL,
    ["station_kit"],
    n=3,
    overrides={
        "ln_k_ref_per_s": np.array([[-16.0, -14.0], [-15.2, -13.0], [-17.1, -15.0]]),
        "ea_j_per_mol": np.array([[85000.0, 0.0], [70000.0, 0.0], [99000.0, 0.0]]),
        "b_per_percent_rh": np.array([[0.035, 0.0], [0.02, 0.0], [0.05, 0.0]]),
        "rh_offset_percent": {"station_kit": np.array([0.0, 3.0, -4.0])},
        "temperature_offset_k": {"station_kit": np.array([0.0, -0.4, 0.7])},
    },
)


@given(st.lists(segment_values, min_size=1, max_size=12), packages)
def test_potency_is_non_increasing_in_time(values: list[tuple[float, float, float]], package: str) -> None:
    segments = _segments(values, package)
    previous = np.full(3, math.inf)
    for end in range(1, len(segments) + 1):
        potency = _evaluate(MODEL, segments[:end], DRAWS).potency_active_percent
        assert bool(np.all(potency <= previous * (1.0 + 1e-14)))
        previous = potency


@given(
    st.lists(segment_values, min_size=1, max_size=8),
    packages,
    st.integers(min_value=0, max_value=7),
    st.floats(min_value=0.05, max_value=0.95),
)
def test_evaluate_draws_split_invariance(
    values: list[tuple[float, float, float]], package: str, which: int, fraction: float
) -> None:
    """Splitting a segment in two leaves every pathway's Theta unchanged (exact
    relaxation inside a segment): 1e-12 relative for the memoryless packs, 1e-11 for the
    exchanging ones (two J evaluations, possibly from different branches)."""
    segments = _segments(values, package)
    index = which % len(segments)
    target = segments[index]
    cut = target.t0_s + fraction * (target.t1_s - target.t0_s)
    split = [
        *segments[:index],
        dataclasses.replace(target, t1_s=cut),
        dataclasses.replace(target, t0_s=cut),
        *segments[index + 1 :],
    ]
    whole = _evaluate(MODEL, segments, DRAWS).theta
    parts = _evaluate(MODEL, split, DRAWS).theta
    rel = 1e-12 if package in ("original_blister", "original_vial") else 1e-11
    assert np.allclose(parts, whole, rtol=rel, atol=0.0)


@given(st.lists(st.floats(min_value=1e-5, max_value=1e8), min_size=1, max_size=20))
def test_placard_shares_are_normalized_and_clamped(durations: list[float]) -> None:
    model = b.thermal_model()
    segments = []
    start = 0.0
    for duration in durations:
        segments.append(b.segment(start, start + duration, temperature=340.0, rh=95.0))
        start += duration
    history = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(segments, PACKAGES)
    nominal = kinetics.nominal_thetas(model, history, trace, ZONES, PACKAGES, BUNDLE.settings)
    placards = kinetics.placards_for(model, history, nominal, BUNDLE.settings)
    assert placards
    assert all(0.0 <= placard.share_nominal <= 1.0 for placard in placards)

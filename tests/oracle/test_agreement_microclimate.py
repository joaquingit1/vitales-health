# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Engine microclimate and MKT against the oracle (spec 3.8, 4, 15.2): 1e-9 relative
(1e-12 absolute near zero); the 40-segment trace of spec 4.6 also at 1e-9 absolute in %RH."""

import math
from collections.abc import Mapping
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pharmacy.config import ConfigBundle
from pharmacy.types import PackageDoc, Segment
from tests.oracle import microclimate as om
from tests.oracle.compare import assert_agrees
from tests.oracle.exposure import mean_kinetic_temperature
from tests.oracle.precision import MP

HYPOTHESIS = settings(derandomize=True, max_examples=200, deadline=None)
RH_ABS = 1e-9


@pytest.fixture(scope="module")
def mc() -> ModuleType:
    import pharmacy.microclimate

    return pharmacy.microclimate


@pytest.fixture(scope="module")
def packages(bundle: ConfigBundle) -> dict[str, PackageDoc]:
    return {p["package_id"]: p for p in bundle.packages["packages"]}


def segment(
    t0: float,
    t1: float,
    zone: str,
    package: str,
    temp: float,
    rh: float,
    low: float | None = None,
    gaps: tuple[str, ...] = (),
) -> Segment:
    return Segment(t0, t1, zone, package, temp, rh, rh if low is None else low, 0.0, 0.0, 0.0, gaps, ())  # type: ignore[arg-type]


def history(packages_cycle: tuple[str, ...], n: int = 40) -> list[Segment]:
    """A crafted history: varying durations, temperatures and RH, two zones, RH gaps with a
    low variant, and package changes (repackaging) along the way."""
    out = []
    t = 0.0
    for i in range(n):
        duration = 1800.0 * (1 + (i * 7) % 13) * (40 if i % 9 == 0 else 1)
        zone = "transit_kit" if i < 12 else "moon_base_pharmacy"
        gap = i in (5, 6, 21, 33)
        rh = 15.0 + (i * 17) % 70
        temp = 285.0 + (i * 5) % 33
        gaps: tuple[str, ...] = ("relative_humidity",) if gap else ()
        package = packages_cycle[(i * len(packages_cycle)) // n]
        out.append(
            segment(t, t + duration, zone, package, temp, 75.0 if gap else rh, 10.0 if gap else None, gaps)
        )
        t += duration
    return out


PACKAGE_CYCLES = [
    ("original_bottle",),
    ("repackaged_bag",),
    ("original_blister",),
    ("original_vial",),
    ("original_bottle", "repackaged_bag"),
    ("repackaged_bag", "original_bottle", "original_blister", "repackaged_bag"),
]


@pytest.mark.parametrize("cycle", PACKAGE_CYCLES, ids="+".join)
def test_internal_rh_trace_forty_segments(
    mc: ModuleType, packages: Mapping[str, PackageDoc], cycle: tuple[str, ...]
) -> None:
    segments = history(cycle)
    engine = mc.internal_rh_trace(segments, packages)
    oracle = om.internal_rh_trace(segments, packages)
    assert engine.package_ids == oracle.package_ids
    assert engine.moisture_kind == oracle.moisture_kind
    for name in ("rh_in_start_high", "rh_in_start_low", "rh_in_end_high", "rh_in_end_low"):
        for i, (e, o) in enumerate(zip(getattr(engine, name), getattr(oracle, name), strict=True)):
            inputs = {"cycle": cycle, "member": name, "segment": i}
            assert_agrees("microclimate.internal_rh_trace", inputs, e, o, "4.3", abs_tol=RH_ABS)
            assert_agrees("microclimate.internal_rh_trace", inputs, e, o, "4.3")
    for i, (e, o) in enumerate(zip(engine.time_constant_s, oracle.time_constant_s, strict=True)):
        if MP.isinf(o):
            assert e == math.inf
        else:
            assert_agrees("microclimate.internal_rh_trace", {"cycle": cycle, "tau_m": i}, e, o, "4.2")


def test_internal_rh_trace_initial_override(mc: ModuleType, packages: Mapping[str, PackageDoc]) -> None:
    segments = history(("original_bottle", "repackaged_bag"))
    override = {"original_bottle": 55.5}
    engine = mc.internal_rh_trace(segments, packages, override)
    oracle = om.internal_rh_trace(segments, packages, override)
    for e, o in zip(engine.rh_in_end_high, oracle.rh_in_end_high, strict=True):
        assert_agrees("microclimate.internal_rh_trace", {"override": override}, e, o, "4.3")


@pytest.mark.parametrize("cycle", PACKAGE_CYCLES, ids="+".join)
@pytest.mark.parametrize("zone", ["transit_kit", "moon_base_pharmacy"])
@pytest.mark.parametrize("tracks", [True, False])
def test_unit_step_response(
    mc: ModuleType, packages: Mapping[str, PackageDoc], cycle: tuple[str, ...], zone: str, tracks: bool
) -> None:
    segments = history(cycle)
    taus = om.internal_rh_trace(segments, packages).time_constant_s
    taus_float = [math.inf if MP.isinf(t) else float(t) for t in taus]
    engine = mc.unit_step_response(segments, taus_float, zone, first_start_tracks_outside=tracks)
    oracle = om.unit_step_response(segments, taus_float, zone, first_start_tracks_outside=tracks)
    assert len(engine) == len(oracle)
    for i, (e, o) in enumerate(zip(engine, oracle, strict=True)):
        assert_agrees(
            "microclimate.unit_step_response",
            {"cycle": cycle, "zone": zone, "tracks": tracks, "segment": i},
            e,
            o,
            "4.4",
        )


@HYPOTHESIS
@given(
    start=st.floats(0.0, 100.0),
    out=st.floats(0.0, 100.0),
    duration=st.floats(0.0, 1e8),
    tau=st.one_of(st.floats(1.0, 1e9), st.just(math.inf), st.just(0.0)),
)
def test_relax_rh_hypothesis(start: float, out: float, duration: float, tau: float) -> None:
    from pharmacy import microclimate

    engine = microclimate.relax_rh(start, out, duration, tau)
    oracle = om.relax_rh(start, out, duration, MP.inf if math.isinf(tau) else tau)
    assert_agrees("microclimate.relax_rh", locals(), engine, oracle, "4.2")


@pytest.mark.parametrize("package_id", ["original_bottle", "repackaged_bag"])
@pytest.mark.parametrize("temperature", [250.0, 298.15, 311.15, 330.0])
def test_time_constant_and_parts(
    mc: ModuleType, packages: Mapping[str, PackageDoc], package_id: str, temperature: float
) -> None:
    package = packages[package_id]
    moisture = package["moisture_model"]
    assert_agrees(
        "microclimate.moisture_capacity_mg_per_percent_rh",
        {"package": package_id},
        mc.moisture_capacity_mg_per_percent_rh(moisture),
        om.moisture_capacity(moisture),
        "4.2",
    )
    assert_agrees(
        "microclimate.permeance_mg_per_s_per_percent_rh",
        {"package": package_id, "T": temperature},
        mc.permeance_mg_per_s_per_percent_rh(moisture, temperature),
        om.permeance(moisture, temperature),
        "4.2",
    )
    assert_agrees(
        "microclimate.exchange_time_constant_s",
        {"package": package_id, "T": temperature},
        mc.exchange_time_constant_s(package, temperature),
        om.exchange_time_constant(package, temperature),
        "4.2",
    )


def test_time_constant_limits(mc: ModuleType, packages: Mapping[str, PackageDoc]) -> None:
    assert mc.exchange_time_constant_s(packages["original_blister"], 300.0) == math.inf
    assert mc.exchange_time_constant_s(packages["original_vial"], 300.0) == 0.0


def test_missing_package_is_a_domain_error(mc: ModuleType, packages: Mapping[str, PackageDoc]) -> None:
    from pharmacy.errors import ModelDomainError

    bad = Segment(0.0, 10.0, "z", None, 300.0, 40.0, 40.0, 0.0, 0.0, 0.0, (), ())
    with pytest.raises(ModelDomainError):
        mc.internal_rh_trace([bad], packages)


# =========================================================================================
# MKT (spec 3.8)
# =========================================================================================


@HYPOTHESIS
@given(
    series=st.lists(st.tuples(st.floats(230.0, 360.0), st.floats(1.0, 1e7)), min_size=1, max_size=30),
    dh=st.sampled_from([83144.0, 60000.0, 120000.0, 200000.0]),
)
def test_mkt_hypothesis(series: list[tuple[float, float]], dh: float) -> None:
    from pharmacy import exposure

    temps = [s[0] for s in series]
    durations = [s[1] for s in series]
    engine = exposure.mean_kinetic_temperature(temps, durations, dh)
    oracle = mean_kinetic_temperature(temps, durations, dh)
    assert_agrees(
        "exposure.mean_kinetic_temperature", {"T": temps, "tau": durations, "dH": dh}, engine, oracle, "3.8"
    )


@pytest.mark.parametrize("vector_id", ["TV-MKT-2", "TV-MKT-3", "TV-MKT-7"])
def test_mkt_vectors_against_oracle(vector: Any, vector_id: str) -> None:
    from pharmacy import exposure

    v = vector(vector_id)["inputs"]
    engine = exposure.mean_kinetic_temperature(
        v["temperatures_k"], v["durations_s"], v["activation_energy_j_per_mol"]
    )
    oracle = mean_kinetic_temperature(v["temperatures_k"], v["durations_s"], v["activation_energy_j_per_mol"])
    assert_agrees("exposure.mean_kinetic_temperature", {"vector": vector_id}, engine, oracle, "3.8")


def test_mkt_extreme_values_do_not_underflow() -> None:
    from pharmacy import exposure

    temps, durations = [20.0, 25.0], [1.0, 1.0]
    engine = exposure.mean_kinetic_temperature(temps, durations, 83144.0)
    oracle = mean_kinetic_temperature(temps, durations, 83144.0)
    assert_agrees("exposure.mean_kinetic_temperature", {"T": temps}, engine, oracle, "3.8 (log-sum-exp)")


@pytest.mark.parametrize(
    ("temps", "durations"),
    [([], []), ([300.0], [1.0, 2.0]), ([0.0], [1.0]), ([300.0], [-1.0]), ([300.0], [0.0])],
)
def test_mkt_input_errors(temps: list[float], durations: list[float]) -> None:
    from pharmacy import exposure
    from pharmacy.errors import InputError

    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature(temps, durations, 83144.0)

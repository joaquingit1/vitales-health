# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Simulator self-tests (spec 15.4) and the error paths of ``pharmacy.envsim`` (spec 13.1).

The 30-day self-test mission (one zone per environment profile, seed 20280110, seed label =
profile id) was fixed before the first run. The variance criterion uses the
sampling-distribution band of spec 15.4;
a long-window test also checks variance and autocorrelation.
"""

import copy
import itertools
import math
from collections.abc import Callable
from typing import Any, cast

import numpy as np
import pytest

from pharmacy import envsim
from pharmacy.config import ConfigBundle, schema_violations
from pharmacy.errors import InputError
from pharmacy.types import (
    EnvironmentProfilesDoc,
    ScenarioDoc,
    SensorSeries,
    SpeTemplate,
    ThermalFailureTemplate,
    iso_to_seconds,
    seconds_to_iso,
)
from tests.scenarios import builders

DAY = 86400.0


@pytest.fixture(scope="module")
def mission(bundle: ConfigBundle) -> ScenarioDoc:
    return builders.self_test_scenario(bundle)


@pytest.fixture(scope="module")
def simulated(bundle: ConfigBundle, mission: ScenarioDoc) -> envsim.SimulatedScenario:
    return envsim.simulate_scenario(mission, bundle.environment_profiles, bundle.zones)


def _profile(bundle: ConfigBundle, profile_id: str) -> Any:
    return next(p for p in bundle.environment_profiles["profiles"] if p["profile_id"] == profile_id)


def _ou_component(
    bundle: ConfigBundle, sim: envsim.SimulatedScenario, zone_id: str, quantity: str
) -> np.ndarray:
    profile_id = next(p for p, z in builders.PROFILE_ZONES.items() if z == zone_id)
    profile = _profile(bundle, profile_id)
    times, values = builders.truth_values(sim, zone_id, cast(Any, quantity))
    t = np.asarray(times)
    phase = np.sin(2 * math.pi * (t - t[0]) / DAY)
    if quantity == "temperature_k":
        base = (
            profile["temperature"]["setpoint_k"]["value"]
            + profile["temperature"]["diurnal_amplitude_k"]["value"] * phase
        )
    else:
        rh = profile["relative_humidity"]
        base = rh["setpoint_percent"]["value"] + rh["diurnal_amplitude_percent"]["value"] * phase
    residual: np.ndarray = np.asarray(values, dtype=np.float64) - base
    return residual


def _ou_parameters(bundle: ConfigBundle, zone_id: str, quantity: str) -> tuple[float, float]:
    profile_id = next(p for p, z in builders.PROFILE_ZONES.items() if z == zone_id)
    profile = _profile(bundle, profile_id)
    if quantity == "temperature_k":
        return profile["temperature"]["ou_sd_k"]["value"], profile["temperature"]["ou_time_constant_s"][
            "value"
        ]
    rh = profile["relative_humidity"]
    return rh["ou_sd_percent"]["value"], rh["ou_time_constant_s"]["value"]


# =========================================================================================
# 15.4: mean, variance
# =========================================================================================


@pytest.mark.parametrize("zone_id", sorted(builders.PROFILE_ZONES.values()))
def test_mean_temperature_within_three_standard_errors(
    bundle: ConfigBundle, simulated: envsim.SimulatedScenario, zone_id: str
) -> None:
    profile_id = next(p for p, z in builders.PROFILE_ZONES.items() if z == zone_id)
    setpoint = _profile(bundle, profile_id)["temperature"]["setpoint_k"]["value"]
    sigma, theta = _ou_parameters(bundle, zone_id, "temperature_k")
    _, values = builders.truth_values(simulated, zone_id, "temperature_k")
    span = builders.SELF_TEST_DAYS * DAY
    standard_error = sigma * math.sqrt(2 * theta / span)
    assert abs(float(np.mean(values)) - setpoint) <= 3 * standard_error


@pytest.mark.parametrize("zone_id", sorted(builders.PROFILE_ZONES.values()))
@pytest.mark.parametrize("quantity", ["temperature_k", "relative_humidity"])
def test_ou_variance_sampling_distribution_band(
    bundle: ConfigBundle, simulated: envsim.SimulatedScenario, zone_id: str, quantity: str
) -> None:
    sigma, tau = _ou_parameters(bundle, zone_id, quantity)
    x = _ou_component(bundle, simulated, zone_id, quantity)
    ratio = float(np.var(x, ddof=1)) / sigma**2
    band = 3.29 * math.sqrt(2 * tau / (builders.SELF_TEST_DAYS * DAY))
    assert abs(ratio - 1.0) <= band, f"variance ratio {ratio:.3f}, band 1 +- {band:.3f}"


@pytest.mark.parametrize("quantity", ["temperature_k", "relative_humidity"])
def test_ou_variance_and_autocorrelation_long_window(bundle: ConfigBundle, quantity: str) -> None:
    """3000 days: the stationary variance within 3 sd of its sampling distribution
    (sd ~ sigma^2 sqrt(2 theta / D)) and the lag-1 autocorrelation within 3 sd of
    e^(-dt/theta)."""
    scenario = builders.self_test_scenario(bundle, days=3000, profiles=("station",))
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    sigma, theta = _ou_parameters(bundle, "station_kit", quantity)
    x = _ou_component(bundle, sim, "station_kit", quantity)
    span = 3000 * DAY
    ratio = float(np.var(x, ddof=1)) / sigma**2
    assert abs(ratio - 1.0) <= 3 * math.sqrt(2 * theta / span)
    lag1 = float(np.corrcoef(x[:-1], x[1:])[0, 1])
    rho = math.exp(-1800.0 / theta)
    assert abs(lag1 - rho) <= 3 * math.sqrt((1 - rho**2) * (2 * theta / span))


def test_relative_humidity_is_clipped_to_physical_range(bundle: ConfigBundle) -> None:
    profiles = copy.deepcopy(bundle.environment_profiles)
    for p in profiles["profiles"]:
        p["relative_humidity"]["setpoint_percent"]["value"] = 99.0
    scenario = builders.self_test_scenario(bundle, days=5, profiles=("station",))
    sim = envsim.simulate_scenario(scenario, profiles, bundle.zones)
    _, values = builders.truth_values(sim, "station_kit", "relative_humidity")
    assert max(values) == 100.0
    readings = [p.value for s in sim.series if s.quantity == "relative_humidity" for p in s.points]
    assert max(readings) <= 100.0
    assert min(readings) >= 0.0


# =========================================================================================
# 15.4: SPE closed form and trapezoid
# =========================================================================================


def _spe(bundle: ConfigBundle, template_id: str) -> SpeTemplate:
    return next(t for t in bundle.environment_profiles["spe_templates"] if t["template_id"] == template_id)


@pytest.mark.parametrize(
    "template_id", ["aug1972_behind_10_g_cm2", "aug1972_behind_20_g_cm2", "mars_surface_sep_2017"]
)
@pytest.mark.parametrize("scale", [1.0, 0.37, None])
def test_spe_closed_form_total(bundle: ConfigBundle, template_id: str, scale: float | None) -> None:
    template = _spe(bundle, template_id)
    total = envsim.spe_dose_gy(1000.0, math.inf, 1000.0, template, scale)
    expected = template["total_absorbed_dose_gy"]["value"] * (1.0 if scale is None else scale)
    assert abs(total - expected) <= 1e-12 * expected
    # pieces add up and the part before the start is zero
    split = envsim.spe_dose_gy(1000.0, 5000.0, 1000.0, template, scale) + envsim.spe_dose_gy(
        5000.0, math.inf, 1000.0, template, scale
    )
    assert abs(split - expected) <= 1e-12 * expected
    assert envsim.spe_dose_gy(0.0, 999.0, 1000.0, template, scale) == 0.0


def test_spe_trapezoid_on_60_s_grid_matches_template(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=12, profiles=("moon",))
    start = iso_to_seconds(builders.SELF_TEST_START_UTC) + DAY
    template = _spe(bundle, "aug1972_behind_20_g_cm2")
    scenario = builders.with_events(
        scenario,
        [
            _event(
                "spe",
                "solar_particle_event",
                "moon_base_pharmacy",
                start,
                template_id=template["template_id"],
                shielding_scale=1.0,
            )
        ],
    )
    truth = envsim.simulate_truth(scenario, bundle.environment_profiles, bundle.zones, 60.0)
    series = next(t for t in truth if t.quantity == "dose_rate_gy_per_s")
    gcr = _profile(bundle, "moon")["radiation"]["absorbed_dose_rate_gy_per_day"]["value"] / DAY
    t = np.asarray(series.t_s)
    pulse = np.asarray(series.values) - gcr
    trapezoid = float(np.sum((pulse[1:] + pulse[:-1]) / 2 * np.diff(t)))
    expected = template["total_absorbed_dose_gy"]["value"]
    assert abs(trapezoid - expected) <= 1e-3 * expected


def test_spe_rate_shape(bundle: ConfigBundle) -> None:
    template = _spe(bundle, "aug1972_behind_10_g_cm2")
    peak = envsim.spe_peak_dose_rate_gy_per_s(template, 1.0)
    rise = template["rise_s"]["value"]
    tau = template["decay_time_constant_s"]["value"]
    t = np.array([-1.0, 0.0, rise / 2, rise, rise + tau])
    rate = envsim.spe_dose_rate(t, 0.0, template, 1.0)
    assert rate[0] == 0.0
    assert rate[1] == 0.0
    assert rate[2] == pytest.approx(peak / 2, rel=1e-15)
    assert rate[3] == pytest.approx(peak, rel=1e-15)
    assert rate[4] == pytest.approx(peak / math.e, rel=1e-15)


def test_spe_with_instant_rise(bundle: ConfigBundle) -> None:
    template = copy.deepcopy(_spe(bundle, "aug1972_behind_10_g_cm2"))
    template["rise_s"]["value"] = 0.0
    total = envsim.spe_dose_gy(0.0, math.inf, 0.0, template, 2.0)
    assert total == pytest.approx(2 * template["total_absorbed_dose_gy"]["value"], rel=1e-12)
    rate = envsim.spe_dose_rate(np.array([0.0]), 0.0, template, 2.0)
    assert rate[0] == envsim.spe_peak_dose_rate_gy_per_s(template, 2.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [("rise_s", -1.0), ("decay_time_constant_s", 0.0), ("decay_time_constant_s", math.inf)],
)
def test_spe_invalid_template(bundle: ConfigBundle, field: str, value: float) -> None:
    template = copy.deepcopy(_spe(bundle, "aug1972_behind_10_g_cm2"))
    cast(Any, template)[field]["value"] = value
    with pytest.raises(InputError):
        envsim.spe_peak_dose_rate_gy_per_s(template, 1.0)
    with pytest.raises(InputError):
        envsim.spe_peak_dose_rate_gy_per_s(_spe(bundle, "aug1972_behind_10_g_cm2"), -1.0)


def test_spe_reversed_interval_is_an_error(bundle: ConfigBundle) -> None:
    with pytest.raises(InputError):
        envsim.spe_dose_gy(10.0, 5.0, 0.0, _spe(bundle, "aug1972_behind_10_g_cm2"), 1.0)


# =========================================================================================
# Thermal failures and light
# =========================================================================================


def _thermal(bundle: ConfigBundle, template_id: str) -> ThermalFailureTemplate:
    return next(
        t for t in bundle.environment_profiles["thermal_failure_templates"] if t["template_id"] == template_id
    )


def test_thermal_failure_trapezoid_and_plateau(bundle: ConfigBundle) -> None:
    template = _thermal(bundle, "tcs_failure_hot")
    ramp, hold, recover = (
        template["ramp_s"]["value"],
        template["hold_s"]["value"],
        template["recover_s"]["value"],
    )
    t = np.array(
        [-1.0, 0.0, ramp / 2, ramp, ramp + hold / 2, ramp + hold + recover / 2, ramp + hold + recover]
    )
    s = envsim.thermal_failure_weight(t, 0.0, template)
    assert math.isnan(s[0])
    assert math.isnan(s[-1])
    assert list(s[1:-1]) == pytest.approx([0.0, 0.5, 1.0, 1.0, 0.5])
    scenario = builders.self_test_scenario(bundle, days=10, profiles=("moon",))
    start = iso_to_seconds(builders.SELF_TEST_START_UTC) + DAY
    scenario = builders.with_events(
        scenario,
        [
            _event(
                "tcs", "thermal_control_failure", "moon_base_pharmacy", start, template_id="tcs_failure_hot"
            )
        ],
    )
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    times, values = builders.truth_values(sim, "moon_base_pharmacy", "temperature_k")
    plateau = [v for tt, v in zip(times, values, strict=True) if start + ramp <= tt < start + ramp + hold]
    assert plateau
    assert all(v == template["target_temperature_k"]["value"] for v in plateau)


def test_thermal_failure_with_target_rh_and_instant_edges(bundle: ConfigBundle) -> None:
    profiles = copy.deepcopy(bundle.environment_profiles)
    template = next(
        t for t in profiles["thermal_failure_templates"] if t["template_id"] == "tcs_failure_cold"
    )
    template["ramp_s"]["value"] = 0.0
    template["recover_s"]["value"] = 0.0
    target_rh = copy.deepcopy(template["target_temperature_k"])
    target_rh["value"] = 80.0
    target_rh["unit"] = "percent"
    template["target_rh_percent"] = target_rh
    scenario = builders.self_test_scenario(bundle, days=12, profiles=("moon",))
    start = iso_to_seconds(builders.SELF_TEST_START_UTC) + DAY
    scenario = builders.with_events(
        scenario,
        [
            _event(
                "cold", "thermal_control_failure", "moon_base_pharmacy", start, template_id="tcs_failure_cold"
            )
        ],
    )
    sim = envsim.simulate_scenario(scenario, profiles, bundle.zones)
    hold = template["hold_s"]["value"]
    times, rh = builders.truth_values(sim, "moon_base_pharmacy", "relative_humidity")
    inside = [v for tt, v in zip(times, rh, strict=True) if start <= tt < start + hold]
    assert inside
    assert all(v == 80.0 for v in inside)
    weight = envsim.thermal_failure_weight(np.array([start, start + hold]), start, template)
    assert weight[0] == 1.0
    assert math.isnan(weight[1])


def test_thermal_negative_duration_is_an_error(bundle: ConfigBundle) -> None:
    template = copy.deepcopy(_thermal(bundle, "tcs_failure_hot"))
    template["hold_s"]["value"] = -1.0
    with pytest.raises(InputError):
        envsim.thermal_failure_weight(np.array([0.0]), 0.0, template)


def test_latest_thermal_event_wins(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=12, profiles=("moon",))
    start = iso_to_seconds(builders.SELF_TEST_START_UTC) + DAY
    scenario = builders.with_events(
        scenario,
        [
            _event(
                "hot", "thermal_control_failure", "moon_base_pharmacy", start, template_id="tcs_failure_hot"
            ),
            _event(
                "cold",
                "thermal_control_failure",
                "moon_base_pharmacy",
                start + DAY,
                template_id="tcs_failure_cold",
            ),
        ],
    )
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    cold = _thermal(bundle, "tcs_failure_cold")
    times, values = builders.truth_values(sim, "moon_base_pharmacy", "temperature_k")
    ramp = cold["ramp_s"]["value"]
    probe = start + DAY + ramp + 3600.0
    value = values[times.index(probe)]
    assert value == cold["target_temperature_k"]["value"]


def test_light_cycle_and_light_change(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=3, profiles=("mars",))
    t0 = iso_to_seconds(builders.SELF_TEST_START_UTC)
    light = _profile(bundle, "mars")["light"]
    hours = light["hours_on_per_day"]["value"]
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    times, lux = builders.truth_values(sim, "mars_base_pharmacy", "illuminance_lux")
    for t, v in zip(times, lux, strict=True):
        on = ((t - t0 - 6 * 3600.0) % DAY) < hours * 3600.0
        assert v == (light["illuminance_lux"]["value"] if on else 0.0)
    _, uv = builders.truth_values(sim, "mars_base_pharmacy", "uv_w_per_m2")
    assert max(uv) == light["uv_w_per_m2"]["value"]
    # a light change switches the light on in the dark hours
    dark = t0 + 2 * 3600.0
    changed = builders.with_events(
        scenario, [_event("lights", "light_exposure_change", "mars_base_pharmacy", dark, duration_s=3600.0)]
    )
    sim2 = envsim.simulate_scenario(changed, bundle.environment_profiles, bundle.zones)
    times2, lux2 = builders.truth_values(sim2, "mars_base_pharmacy", "illuminance_lux")
    assert lux2[times2.index(dark)] == light["illuminance_lux"]["value"]
    assert lux[times.index(dark)] == 0.0
    readings = next(s for s in sim2.series if s.quantity == "illuminance_lux")
    assert {p.value for p in readings.points} <= {0.0, light["illuminance_lux"]["value"]}


def test_light_change_errors(bundle: ConfigBundle) -> None:
    moon = builders.self_test_scenario(bundle, days=2, profiles=("moon",))
    t = iso_to_seconds(builders.SELF_TEST_START_UTC) + 3600.0
    no_light = builders.with_events(
        moon, [_event("l", "light_exposure_change", "moon_base_pharmacy", t, duration_s=60.0)]
    )
    with pytest.raises(InputError, match="has no light"):
        envsim.simulate_truth(no_light, bundle.environment_profiles, bundle.zones, 1800.0)
    mars = builders.self_test_scenario(bundle, days=2, profiles=("mars",))
    no_duration = builders.with_events(mars, [_event("l", "light_exposure_change", "mars_base_pharmacy", t)])
    with pytest.raises(InputError, match="duration"):
        envsim.simulate_truth(no_duration, bundle.environment_profiles, bundle.zones, 1800.0)


# =========================================================================================
# 15.4: outage, determinism, dropout
# =========================================================================================


def _event(
    event_id: str,
    kind: str,
    zone_id: str,
    start_s: float,
    *,
    duration_s: float | None = None,
    template_id: str | None = None,
    sensor_ids: list[str] | None = None,
    shielding_scale: float | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "kind": kind,
        "zone_id": zone_id,
        "start_utc": seconds_to_iso(start_s),
        "duration_s": duration_s,
        "template_id": template_id,
        "sensor_ids": sensor_ids or [],
        "shielding_scale": shielding_scale,
        "note": "",
    }


def _largest_gaps(series: SensorSeries, interval: float) -> list[tuple[float, float]]:
    t = [p.t_s for p in series.points]
    return [(a, b - a - interval) for a, b in itertools.pairwise(t) if b - a > 2 * interval]


def test_outage_produces_one_gap_of_scripted_length(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=6, profiles=("moon",))
    start = iso_to_seconds(builders.SELF_TEST_START_UTC) + 2 * DAY + 900.0
    duration = 14400.0
    scenario = builders.with_events(
        scenario,
        [
            _event(
                "out",
                "sensor_outage",
                "moon_base_pharmacy",
                start,
                duration_s=duration,
                sensor_ids=["mbp-t1", "mbp-rh1"],
            )
        ],
    )
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    for series in sim.series:
        gaps = _largest_gaps(series, 1800.0)
        if series.sensor_id in ("mbp-t1", "mbp-rh1"):
            assert len(gaps) == 1, gaps
            gap_start, gap_length = gaps[0]
            assert abs(gap_length - duration) <= 1800.0
            assert start - 1800.0 <= gap_start < start
            assert not any(start <= p.t_s < start + duration for p in series.points)
        else:
            assert gaps == []


def test_same_seed_gives_identical_series(
    bundle: ConfigBundle, mission: ScenarioDoc, simulated: envsim.SimulatedScenario
) -> None:
    again = envsim.simulate_scenario(mission, bundle.environment_profiles, bundle.zones)
    assert again == simulated
    other = copy.deepcopy(mission)
    other["mission"]["seed"] += 1
    different = envsim.simulate_scenario(other, bundle.environment_profiles, bundle.zones)
    assert different.series != simulated.series


def test_dropout_fraction_within_three_binomial_standard_errors(
    bundle: ConfigBundle, simulated: envsim.SimulatedScenario
) -> None:
    p = bundle.environment_profiles["profiles"][0]["sensor_defaults"]["dropout_probability"]["value"]
    assert all(
        pr["sensor_defaults"]["dropout_probability"]["value"] == p
        for pr in bundle.environment_profiles["profiles"]
    )
    instants = round(builders.SELF_TEST_DAYS * DAY / 1800.0) + 1
    total = instants * len(simulated.series)
    kept = sum(len(s.points) for s in simulated.series)
    fraction = (total - kept) / total
    assert abs(fraction - p) <= 3 * math.sqrt(p * (1 - p) / total)


def test_series_ids_units_and_timestamps(simulated: envsim.SimulatedScenario) -> None:
    for s in simulated.series:
        assert s.series_id == f"envsim-self-test:{s.sensor_id}"
        assert s.unit == envsim.UNITS[s.quantity]
        times = [p.t_s for p in s.points]
        assert times == sorted(set(times))
        assert all(iso_to_seconds(p.t_utc) == p.t_s for p in s.points)


def test_measurement_noise_bias_and_quantization(
    bundle: ConfigBundle, simulated: envsim.SimulatedScenario
) -> None:
    defaults = _profile(bundle, "station")["sensor_defaults"]
    series = next(s for s in simulated.series if s.sensor_id == "stk-t1")
    times, values = builders.truth_values(simulated, "station_kit", "temperature_k")
    truth = np.interp([p.t_s for p in series.points], times, values)
    residual = np.asarray([p.value for p in series.points]) - truth
    resolution = defaults["temperature_resolution_k"]["value"]
    noise = defaults["temperature_noise_sd_k"]["value"]
    quant_sd = resolution / math.sqrt(12)
    sd = float(np.std(residual, ddof=1))
    assert abs(sd - math.hypot(noise, quant_sd)) <= 4 * math.hypot(noise, quant_sd) / math.sqrt(
        2 * residual.size
    )
    bias = float(np.mean(residual))
    assert abs(bias) <= 4 * defaults["sensor_bias_temperature_sd_k"]["value"]
    on_grid = np.asarray([p.value for p in series.points]) / resolution
    assert np.allclose(on_grid, np.round(on_grid), atol=1e-6)
    dose = next(s for s in simulated.series if s.sensor_id == "stk-dos1")
    assert all(p.value >= 0.0 for p in dose.points)


def test_generated_inputs_validate_against_the_inputs_schema(
    bundle: ConfigBundle, mission: ScenarioDoc, simulated: envsim.SimulatedScenario
) -> None:
    as_of = seconds_to_iso(iso_to_seconds(builders.SELF_TEST_START_UTC) + 10 * DAY)
    doc = builders.engine_inputs(simulated, as_of)
    assert schema_violations(cast(Any, doc), builders.INPUTS_SCHEMA) == []
    assert all(p["t_s"] <= iso_to_seconds(as_of) for s in doc["sensor_series"] for p in s["points"])
    assert schema_violations(cast(Any, mission), builders.SCENARIO_SCHEMA) == []


def test_demo_simulation_and_inputs_validate(bundle: ConfigBundle) -> None:
    _, scenario, sim = builders.demo_simulation()
    for as_of in scenario["assessment_times_utc"]:
        doc = builders.engine_inputs(sim, as_of)
        assert schema_violations(cast(Any, doc), builders.INPUTS_SCHEMA) == []
    assert len(sim.assays) == 1
    assert len(sim.inspections) == 1
    assert {s.zone_id for s in sim.series} == {"transit_kit", "moon_base_pharmacy"}


def test_summary_statistics(simulated: envsim.SimulatedScenario) -> None:
    stats = envsim.summary_statistics(simulated.series)
    for s in simulated.series:
        row = stats[s.series_id]
        assert row["n"] == len(s.points)
        assert 0.99 < row["coverage_fraction"] <= 1.0
        assert row["min"] <= row["mean"] <= row["max"]
    empty = SensorSeries("e", "x", "z", "temperature_k", "K", ())
    single = SensorSeries("s", "x", "z", "temperature_k", "K", simulated.series[0].points[:1])
    out = envsim.summary_statistics((empty, single))
    assert out["e"] == {"n": 0.0, "coverage_fraction": 0.0}
    assert out["s"]["sd"] == 0.0
    assert out["s"]["coverage_fraction"] == 1.0


# =========================================================================================
# Grid rules and error paths (spec 13.1, branch coverage)
# =========================================================================================


def test_grid_reaches_mission_end_and_sampling_stays_inside(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=1.01, profiles=("moon",))
    truth = envsim.simulate_truth(scenario, bundle.environment_profiles, bundle.zones, 1800.0)
    end = iso_to_seconds(scenario["mission"]["end_utc"])
    assert truth[0].t_s[-1] >= end
    assert truth[0].t_s[-2] < end
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    assert all(p.t_s <= end for s in sim.series for p in s.points)


def test_zone_seed_is_sha256_of_canonical_json() -> None:
    import hashlib

    digest = hashlib.sha256(b'[20280110,"moon","mbp-t1"]').digest()
    assert envsim.zone_seed(20280110, "moon", "mbp-t1") == int.from_bytes(digest[:16], "big")
    digest_zone = hashlib.sha256(b'[20280110,"moon",null]').digest()
    assert envsim.zone_seed(20280110, "moon", None) == int.from_bytes(digest_zone[:16], "big")


def _mutate(bundle: ConfigBundle, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    scenario = cast(dict[str, Any], builders.self_test_scenario(bundle, days=2, profiles=("moon",)))
    change(scenario)
    return scenario


@pytest.mark.parametrize(
    ("label", "change", "message"),
    [
        (
            "end before start",
            lambda s: s["mission"].update(end_utc=s["mission"]["start_utc"]),
            "after mission start",
        ),
        (
            "unknown profile",
            lambda s: s["zones"][0].update(profile_id="venus"),
            "unknown environment profile",
        ),
        ("unknown zone", lambda s: s["zones"][0].update(zone_id="nowhere"), "not in storage-zones"),
        ("duplicate zone", lambda s: s["zones"].append(copy.deepcopy(s["zones"][0])), "unique"),
        (
            "event in unsimulated zone",
            lambda s: s["events"].append(_event("e", "sensor_outage", "station_kit", 0.0, duration_s=1.0)),
            "not simulated",
        ),
        (
            "unknown thermal template",
            lambda s: s["events"].append(
                _event("e", "thermal_control_failure", "moon_base_pharmacy", 1.9e9, template_id="nope")
            ),
            "thermal-failure template",
        ),
        (
            "unknown SPE template",
            lambda s: s["events"].append(
                _event("e", "solar_particle_event", "moon_base_pharmacy", 1.9e9, template_id="x")
            ),
            "SPE template",
        ),
    ],
)
def test_truth_input_errors(
    bundle: ConfigBundle, label: str, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    scenario = _mutate(bundle, change)
    with pytest.raises(InputError, match=message):
        envsim.simulate_truth(cast(ScenarioDoc, scenario), bundle.environment_profiles, bundle.zones, 1800.0)


@pytest.mark.parametrize("step", [0.0, -5.0, math.inf, math.nan])
def test_invalid_grid_step(bundle: ConfigBundle, step: float) -> None:
    scenario = builders.self_test_scenario(bundle, days=1, profiles=("moon",))
    with pytest.raises(InputError):
        envsim.simulate_truth(scenario, bundle.environment_profiles, bundle.zones, step)


def test_invalid_ou_parameters(bundle: ConfigBundle) -> None:
    profiles: EnvironmentProfilesDoc = copy.deepcopy(bundle.environment_profiles)
    for p in profiles["profiles"]:
        p["temperature"]["ou_time_constant_s"]["value"] = 0.0
    scenario = builders.self_test_scenario(bundle, days=1, profiles=("moon",))
    with pytest.raises(InputError, match="OU"):
        envsim.simulate_truth(scenario, profiles, bundle.zones, 1800.0)


def test_single_node_grid_has_no_innovations(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=1, profiles=("moon",))
    truth = envsim.simulate_truth(scenario, bundle.environment_profiles, bundle.zones, 2 * DAY)
    assert len(truth[0].t_s) == 2


@pytest.mark.parametrize(
    ("label", "change", "message"),
    [
        (
            "sensor not configured",
            lambda s: s["zones"][0]["sensors"][0].update(sensor_id="ghost"),
            "not configured",
        ),
        (
            "wrong quantity",
            lambda s: s["zones"][0]["sensors"][0].update(quantity="uv_w_per_m2"),
            "not configured",
        ),
        (
            "outage without duration",
            lambda s: s["events"].append(
                _event("o", "sensor_outage", "moon_base_pharmacy", 1.9e9, sensor_ids=["mbp-t1"])
            ),
            "non-negative duration",
        ),
    ],
)
def test_measurement_input_errors(
    bundle: ConfigBundle, label: str, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    scenario = _mutate(bundle, change)
    with pytest.raises(InputError, match=message):
        envsim.simulate_scenario(cast(ScenarioDoc, scenario), bundle.environment_profiles, bundle.zones)


def test_truth_must_cover_sampling_and_exist(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=2, profiles=("moon",))
    truth = envsim.simulate_truth(scenario, bundle.environment_profiles, bundle.zones, 1800.0)
    short = tuple(envsim.TruthSeries(t.zone_id, t.quantity, t.t_s[:10], t.values[:10]) for t in truth)
    with pytest.raises(InputError, match="does not cover"):
        envsim.measure(short, scenario, bundle.environment_profiles, bundle.zones)
    missing = tuple(t for t in truth if t.quantity != "temperature_k")
    with pytest.raises(InputError, match="no truth"):
        envsim.measure(missing, scenario, bundle.environment_profiles, bundle.zones)


def test_invalid_sensor_resolution(bundle: ConfigBundle) -> None:
    profiles = copy.deepcopy(bundle.environment_profiles)
    for p in profiles["profiles"]:
        p["sensor_defaults"]["temperature_resolution_k"]["value"] = 0.0
    scenario = builders.self_test_scenario(bundle, days=1, profiles=("moon",))
    with pytest.raises(InputError, match="resolution"):
        envsim.simulate_scenario(scenario, profiles, bundle.zones)


def test_scenario_without_sensors_needs_a_grid(bundle: ConfigBundle) -> None:
    scenario = builders.self_test_scenario(bundle, days=1, profiles=("moon",))
    scenario["zones"][0]["sensors"] = []
    with pytest.raises(InputError, match="grid_s"):
        envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones, grid_s=3600.0)
    assert sim.series == ()
    assert len(sim.truth) == 5
